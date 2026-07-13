/** 组合五个职责明确的配置区，并处理向导翻页和最终提交。 */
import type { FormEvent } from "react";
import type { AgentProfileInput, ProviderConfig } from "../../types";
import BasicProfileFields from "./BasicProfileFields";
import ContextMemoryFields from "./ContextMemoryFields";
import ProviderBudgetFields from "./ProviderBudgetFields";
import WorkflowValidationFields from "./WorkflowValidationFields";
import { WIZARD_STEPS } from "./model";

interface Props {
  form: AgentProfileInput;
  providers: ProviderConfig[];
  expert: boolean;
  wizardStep: number;
  schemaText: string;
  preview: string;
  submitLabel: string;
  onChange: (form: AgentProfileInput) => void;
  onWizardStepChange: (step: number) => void;
  onSchemaChange: (value: string) => void;
  onPreview: () => void;
  onSubmit: () => void;
}

export default function AgentProfileForm({
  form,
  providers,
  expert,
  wizardStep,
  schemaText,
  preview,
  submitLabel,
  onChange,
  onWizardStepChange,
  onSchemaChange,
  onPreview,
  onSubmit,
}: Props) {
  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  return (
    <>
      <div className="wizard-progress">
        配置向导 {wizardStep}/5：{WIZARD_STEPS[wizardStep - 1]}
      </div>
      <form className="settings-form" onSubmit={submit}>
        {(expert || wizardStep === 1) && (
          <BasicProfileFields form={form} onChange={onChange} />
        )}
        {(expert || wizardStep === 2) && (
          <ProviderBudgetFields
            form={form}
            providers={providers}
            onChange={onChange}
          />
        )}
        {(expert || wizardStep === 3) && (
          <ContextMemoryFields
            form={form}
            preview={preview}
            onChange={onChange}
            onPreview={onPreview}
          />
        )}
        {(expert || wizardStep === 4) && (
          <WorkflowValidationFields
            form={form}
            schemaText={schemaText}
            onChange={onChange}
            onSchemaChange={onSchemaChange}
          />
        )}
        {(expert || wizardStep === 5) && (
          <fieldset>
            <legend>报告与配置预览</legend>
            <label>
              报告模板
              <textarea
                value={form.report_template}
                onChange={(event) =>
                  onChange({ ...form, report_template: event.target.value })
                }
              />
            </label>
            <pre className="config-preview">
              {JSON.stringify(form, null, 2)}
            </pre>
          </fieldset>
        )}
        {!expert && (
          <div className="wizard-actions">
            <button
              type="button"
              disabled={wizardStep === 1}
              onClick={() => onWizardStepChange(wizardStep - 1)}
            >
              上一步
            </button>
            <button
              type="button"
              disabled={wizardStep === 5}
              onClick={() => onWizardStepChange(wizardStep + 1)}
            >
              下一步
            </button>
          </div>
        )}
        <button className="primary" type="submit">
          {submitLabel}
        </button>
      </form>
    </>
  );
}
