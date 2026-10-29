import { useEffect, useState } from "react";
import { App as AntApp, ConfigProvider, Layout, Steps, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useTranslation } from "react-i18next";
import { ModelSettings } from "./components/ModelSettings";
import { fetchModelConfig, type ModelStatus } from "./services/modelService";
import { ConnectPage } from "./pages/ConnectPage";
import { HomePage } from "./pages/HomePage";
import { WizardPage } from "./pages/WizardPage";
import { SessionPage } from "./pages/SessionPage";
import { nextStep, stepTrail } from "./roles/registry";
import { beginNewSession, getSession, subscribe } from "./store/sessionStore";

const { Content } = Layout;

/** 工作台 → 选助手 → 连接 → 本局选项 → 对话 */
type StepKey = "home" | "assistant" | "connect" | "prefs" | "session";

export default function App() {
  const { t } = useTranslation();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  useEffect(() => { void fetchModelConfig().then(setModelStatus).catch(() => undefined); }, []);
  const [step, setStep] = useState<StepKey>("home");
  const [, tick] = useState(0);
  useEffect(() => subscribe(() => tick((n) => n + 1)), []);
  const roleId = getSession().agentRole;
  const trail = stepTrail(roleId);
  void tick;

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: { colorPrimary: "#1677ff", borderRadius: 6 },
      }}
    >
      <AntApp>
        {settingsOpen && <ModelSettings open onClose={() => setSettingsOpen(false)} onChange={setModelStatus} />}
        <Layout
          className={step === "session" ? "app-shell app-shell-session" : "app-shell"}
        >
          <Content
            className={
              step === "session" ? "app-content app-content-session" : "app-content"
            }
          >
            {step !== "session" && step !== "home" && (
              <Steps
                current={Math.max(0, trail.indexOf(step as (typeof trail)[number]))}
                style={{ marginBottom: 24 }}
                items={trail.map((id) => ({
                  title:
                    id === "assistant"
                      ? t("wizard.assistant")
                      : id === "connect"
                        ? t("connect.title")
                        : id === "prefs"
                          ? t("wizard.prefs")
                          : t("common.start"),
                }))}
              />
            )}
            {step === "home" && (
              <HomePage
                onOpenSettings={() => setSettingsOpen(true)}
                modelStatus={modelStatus}
                onNewSession={() => modelStatus?.configured ? setStep("assistant") : setSettingsOpen(true)}
                onOpenRecent={() => setStep("session")}
              />
            )}
            {step === "assistant" && (
              <WizardPage
                step="assistant"
                onBack={() => setStep("home")}
                onNext={() => {
                  beginNewSession();
                  setStep(nextStep(getSession().agentRole, "assistant"));
                }}
              />
            )}
            {step === "connect" && (
              <ConnectPage
                onBack={() => setStep("home")}
                onNext={() => setStep(nextStep(getSession().agentRole, "connect"))}
              />
            )}
            {step === "prefs" && (
              <WizardPage
                step="prefs"
                onBack={() => setStep("connect")}
                onNext={() => setStep(nextStep(getSession().agentRole, "prefs"))}
              />
            )}
            {step === "session" && <SessionPage onExit={() => setStep("home")} onOpenSettings={() => setSettingsOpen(true)} />}
          </Content>
        </Layout>
      </AntApp>
    </ConfigProvider>
  );
}
