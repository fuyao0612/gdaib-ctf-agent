"""PCAP/PCAPNG 离线摘要分析；不重放流量、不连接网络、不执行载荷。"""

from __future__ import annotations

import ipaddress
import struct
from collections import Counter
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from yuwang.tooling.contracts import ToolCallRequest, ToolSpec

from .base import CtfArtifactTool, ctf_spec

MAX_READ_BYTES = 16 * 1024 * 1024
MAX_PACKETS = 5_000
MAX_FLOWS = 100
MAX_ENDPOINTS = 200


class PcapAnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    max_packets: int = Field(default=2_000, ge=1, le=MAX_PACKETS)
    max_flows: int = Field(default=50, ge=1, le=MAX_FLOWS)


class PcapEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    packets: int = Field(ge=1)


class PcapFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str
    source: str
    destination: str
    packets: int = Field(ge=1)
    bytes: int = Field(ge=0)


class DnsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    destination: str
    name: str = Field(min_length=1, max_length=255)
    record_type: int = Field(ge=0, le=65535)


class HttpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    destination: str
    method: str = Field(min_length=1, max_length=12)
    path: str = Field(min_length=1, max_length=2_048)
    host: str | None = Field(default=None, max_length=255)


class PcapAnalyzeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    format: str
    packet_count: int = Field(ge=0)
    analyzed_packets: int = Field(ge=0)
    captured_bytes: int = Field(ge=0)
    truncated: bool = False
    protocols: list[ProtocolCount] = Field(default_factory=list, max_length=50)
    endpoints: list[PcapEndpoint] = Field(default_factory=list, max_length=MAX_ENDPOINTS)
    flows: list[PcapFlow] = Field(default_factory=list, max_length=MAX_FLOWS)
    dns_queries: list[DnsQuery] = Field(default_factory=list, max_length=100)
    http_requests: list[HttpRequest] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ProtocolCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(min_length=1, max_length=50)
    packets: int = Field(ge=1)


class PcapAnalyzeTool(CtfArtifactTool[PcapAnalyzeInput, PcapAnalyzeOutput]):
    input_model = PcapAnalyzeInput
    output_model = PcapAnalyzeOutput

    @property
    def spec(self) -> ToolSpec:
        return ctf_spec(
            name="network_capture_analyze",
            display_name="PCAP 流量离线分析",
            description="对已授权 PCAP/PCAPNG Artifact 做有界离线包头、协议、端点、DNS 和 HTTP 请求摘要，不重放流量、不连接网络、不执行载荷",
            capabilities=["network_forensics", "pcap", "traffic_summary", "artifact_analysis"],
            scenarios=["forensics", "incident_response", "ctf", "vulnerability_analysis"],
            permissions=["artifact:read"],
            timeout_seconds=30,
            error_codes=["artifact_not_found", "file_too_large", "unsupported_capture", "parse_error", "result_limit"],
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
        )

    async def execute_with_request(
        self, value: PcapAnalyzeInput, request: ToolCallRequest | None
    ) -> PcapAnalyzeOutput:
        artifact, content = self.artifacts.read(value.artifact_id, request, max_bytes=MAX_READ_BYTES)
        return _analyze(artifact.id, content, value)


def _analyze(artifact_id: UUID, content: bytes, options: PcapAnalyzeInput) -> PcapAnalyzeOutput:
    if len(content) < 4:
        raise ValueError("流量文件过小，无法识别 PCAP/PCAPNG")
    if content[:4] == b"\x0a\x0d\x0d\x0a":
        records, packet_count, captured_bytes, fmt, warnings = _parse_pcapng(content, options.max_packets)
    elif content[:4] in {b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"}:
        records, packet_count, captured_bytes, fmt, warnings = _parse_pcap(content, options.max_packets)
    else:
        raise ValueError("仅支持 PCAP 与 PCAPNG 文件")
    protocols: Counter[str] = Counter()
    endpoints: Counter[str] = Counter()
    flows: dict[tuple[str, str, str], list[int]] = {}
    dns_queries: list[DnsQuery] = []
    http_requests: list[HttpRequest] = []
    for payload, linktype in records:
        parsed = _parse_packet(payload, linktype)
        if parsed is None:
            protocols["other"] += 1
            continue
        protocol, source, destination, body = parsed
        protocols[protocol] += 1
        endpoints[source] += 1
        endpoints[destination] += 1
        key = (protocol, source, destination)
        item = flows.setdefault(key, [0, 0])
        item[0] += 1
        item[1] += len(payload)
        if protocol in {"udp", "tcp"}:
            src_host, src_port = _split_endpoint(source)
            dst_host, dst_port = _split_endpoint(destination)
            if src_port == 53 or dst_port == 53:
                query = _dns_query(body, source, destination)
                if query and len(dns_queries) < 100:
                    dns_queries.append(query)
            if protocol == "tcp" and (src_port == 80 or dst_port == 80 or body.startswith((b"GET ", b"POST ", b"PUT ", b"HEAD ", b"DELETE "))):
                request = _http_request(body, source, destination)
                if request and len(http_requests) < 100:
                    http_requests.append(request)
    flow_items = [
        PcapFlow(protocol=protocol, source=source, destination=destination, packets=values[0], bytes=values[1])
        for (protocol, source, destination), values in sorted(flows.items(), key=lambda item: (-item[1][0], item[0]))[: options.max_flows]
    ]
    return PcapAnalyzeOutput(
        artifact_id=artifact_id,
        format=fmt,
        packet_count=packet_count,
        analyzed_packets=len(records),
        captured_bytes=captured_bytes,
        truncated=packet_count > len(records),
        protocols=[ProtocolCount(protocol=name, packets=count) for name, count in protocols.most_common(50)],
        endpoints=[PcapEndpoint(address=address, packets=count) for address, count in endpoints.most_common(MAX_ENDPOINTS)],
        flows=flow_items,
        dns_queries=dns_queries,
        http_requests=http_requests,
        warnings=warnings,
    )


def _parse_pcap(content: bytes, limit: int) -> tuple[list[tuple[bytes, int]], int, int, str, list[str]]:
    magic = content[:4]
    endian = "<" if magic in {b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"} else ">"
    nano = magic in {b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"}
    if len(content) < 24:
        raise ValueError("PCAP 全局头不完整")
    linktype = struct.unpack_from(endian + "I", content, 20)[0]
    offset = 24
    records: list[tuple[bytes, int]] = []
    packet_count = 0
    captured_bytes = 0
    warnings: list[str] = ["时间戳精度：纳秒" if nano else "时间戳精度：微秒"]
    while offset + 16 <= len(content):
        _sec, _usec, included, original = struct.unpack_from(endian + "IIII", content, offset)
        offset += 16
        packet_count += 1
        if included > original or included > len(content) - offset:
            warnings.append("发现不完整的数据包，分析在此处停止")
            break
        captured_bytes += included
        if len(records) < limit:
            records.append((content[offset : offset + included], linktype))
        offset += included
    return records, packet_count, captured_bytes, "pcap", warnings


def _parse_pcapng(content: bytes, limit: int) -> tuple[list[tuple[bytes, int]], int, int, str, list[str]]:
    offset = 0
    endian: str | None = None
    interfaces: dict[int, int] = {}
    records: list[tuple[bytes, int]] = []
    packet_count = 0
    captured_bytes = 0
    warnings: list[str] = []
    while offset + 12 <= len(content):
        block_type = content[offset : offset + 4]
        if block_type == b"\x0a\x0d\x0d\x0a":
            if offset + 12 > len(content):
                break
            magic = content[offset + 8 : offset + 12]
            endian = "<" if magic == b"\x4d\x3c\x2b\x1a" else ">" if magic == b"\x1a\x2b\x3c\x4d" else None
            if endian is None:
                raise ValueError("PCAPNG 字节序标记无效")
        if endian is None:
            raise ValueError("PCAPNG 缺少节头")
        total_length = struct.unpack_from(endian + "I", content, offset + 4)[0]
        if total_length < 12 or total_length % 4 or offset + total_length > len(content):
            warnings.append("PCAPNG 块长度无效，分析在此处停止")
            break
        body = content[offset + 8 : offset + total_length - 4]
        if block_type == b"\x01\x00\x00\x00" and len(body) >= 8:
            interfaces[len(interfaces)] = struct.unpack_from(endian + "H", body, 0)[0]
        elif block_type == b"\x06\x00\x00\x00" and len(body) >= 20:
            interface_id, captured = struct.unpack_from(endian + "II", body, 0)
            packet = body[20 : 20 + captured]
            packet_count += 1
            captured_bytes += len(packet)
            if len(records) < limit:
                records.append((packet, interfaces.get(interface_id, 1)))
        elif block_type == b"\x03\x00\x00\x00" and len(body) >= 4:
            original = struct.unpack_from(endian + "I", body, 0)[0]
            packet = body[4 : 4 + original]
            packet_count += 1
            captured_bytes += len(packet)
            if len(records) < limit:
                records.append((packet, interfaces.get(0, 1)))
        offset += total_length
    return records, packet_count, captured_bytes, "pcapng", warnings


def _parse_packet(packet: bytes, linktype: int) -> tuple[str, str, str, bytes] | None:
    if linktype == 1 and len(packet) >= 14:
        ethertype = struct.unpack_from(">H", packet, 12)[0]
        offset = 14
        if ethertype in {0x8100, 0x88A8} and len(packet) >= 18:
            ethertype = struct.unpack_from(">H", packet, 16)[0]
            offset = 18
        payload = packet[offset:]
    elif linktype in {101, 228}:
        ethertype, payload = 0x0800 if packet and packet[0] >> 4 == 4 else 0x86DD, packet
    else:
        return None
    if ethertype == 0x0800 and len(payload) >= 20:
        ihl = (payload[0] & 0x0F) * 4
        if payload[0] >> 4 != 4 or ihl < 20 or len(payload) < ihl:
            return None
        src = str(ipaddress.ip_address(payload[12:16]))
        dst = str(ipaddress.ip_address(payload[16:20]))
        proto = payload[9]
        return _transport(proto, src, dst, payload[ihl:])
    if ethertype == 0x86DD and len(payload) >= 40 and payload[0] >> 4 == 6:
        src = str(ipaddress.ip_address(payload[8:24]))
        dst = str(ipaddress.ip_address(payload[24:40]))
        return _transport(payload[6], src, dst, payload[40:])
    return None


def _transport(proto: int, src: str, dst: str, payload: bytes) -> tuple[str, str, str, bytes] | None:
    if proto == 6 and len(payload) >= 20:
        src_port, dst_port, offset = struct.unpack_from(">HHB", payload, 0)[0], struct.unpack_from(">HHB", payload, 0)[1], (payload[12] >> 4) * 4
        return "tcp", f"{src}:{src_port}", f"{dst}:{dst_port}", payload[offset:] if 20 <= offset <= len(payload) else b""
    if proto == 17 and len(payload) >= 8:
        src_port, dst_port = struct.unpack_from(">HH", payload, 0)
        return "udp", f"{src}:{src_port}", f"{dst}:{dst_port}", payload[8:]
    return {1: "icmp", 58: "icmpv6"}.get(proto, f"ip-proto-{proto}"), src, dst, payload


def _split_endpoint(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host, int(port) if port.isdigit() else 0


def _dns_query(payload: bytes, source: str, destination: str) -> DnsQuery | None:
    if len(payload) < 12 or payload[2] & 0x80:
        return None
    qdcount = struct.unpack_from(">H", payload, 4)[0]
    if qdcount < 1:
        return None
    offset = 12
    labels: list[str] = []
    for _ in range(50):
        if offset >= len(payload):
            return None
        length = payload[offset]
        offset += 1
        if length == 0:
            break
        if length > 63 or offset + length > len(payload):
            return None
        label = payload[offset : offset + length].decode("ascii", errors="replace")
        try:
            label = label.encode("ascii").decode("idna")
        except UnicodeError:
            pass
        labels.append(label)
        offset += length
    if offset + 4 > len(payload):
        return None
    record_type = struct.unpack_from(">H", payload, offset)[0]
    return DnsQuery(source=source, destination=destination, name=".".join(labels)[:255], record_type=record_type)


def _http_request(payload: bytes, source: str, destination: str) -> HttpRequest | None:
    line = payload.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
    parts = line.split(" ", 2)
    if len(parts) != 3 or parts[0] not in {"GET", "POST", "PUT", "HEAD", "DELETE", "OPTIONS", "PATCH"}:
        return None
    headers = payload.split(b"\r\n\r\n", 1)[0].decode("latin-1", errors="replace").split("\r\n")[1:]
    host = next((item.split(":", 1)[1].strip()[:255] for item in headers if item.casefold().startswith("host:")), None)
    return HttpRequest(source=source, destination=destination, method=parts[0], path=parts[1][:2048], host=host)
