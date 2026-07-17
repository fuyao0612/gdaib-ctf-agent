import type { Dispatch, SetStateAction } from "react";
import { api } from "../api";
import type { Run, RunControl } from "../types";

interface Options {
  run: Run | null;
  setRun: Dispatch<SetStateAction<Run | null>>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string>>;
  loadControl: (runId: string) => Promise<RunControl>;
  connect: (run: Run) => void;
}

/** 集中处理运行控制请求，避免页面协调器重复实现错误与刷新逻辑。 */
export function useRunControlActions(options: Options) {
  const execute = async (action: () => Promise<void>): Promise<boolean> => {
    options.setBusy(true);
    options.setError("");
    try {
      await action();
      return true;
    } catch (cause) {
      options.setError(String(cause));
      return false;
    } finally {
      options.setBusy(false);
    }
  };

  const pause = () => {
    if (!options.run) return Promise.resolve(false);
    return (
    execute(async () => {
      const run = await api.pause(options.run!.id, crypto.randomUUID());
      options.setRun(run);
      await options.loadControl(run.id);
    })
    );
  };

  const resume = () => {
    if (!options.run) return Promise.resolve(false);
    return (
    execute(async () => {
      const run = await api.resume(options.run!.id, crypto.randomUUID());
      options.setRun(run);
      await options.loadControl(run.id);
      options.connect(run);
    })
    );
  };

  const queueGuidance = (content: string) => {
    if (!options.run) return Promise.resolve(false);
    return (
    execute(async () => {
      await api.queueGuidance(options.run!.id, content, crypto.randomUUID());
      await options.loadControl(options.run!.id);
    })
    );
  };

  return { pause, resume, queueGuidance };
}
