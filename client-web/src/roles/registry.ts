/** 真机性能助手（device_perf）：Android 真机对话。当前不连手机、不采帧。 */

export type SetupStep = "connect" | "prefs" | "session";

export type RoleDef = {
  id: string;
  labelKey: string;
  namePrefixKey: string;
  defaultMcpUrl: string;
  /** 选助手之后的步骤，最后一步是进入对话。 */
  steps: SetupStep[];
  siderWidth: number;
  /** 场景优化要等 Unity MCP；真机这一阶段不走编辑器。 */
  requiresMcp: boolean;
};

export const ROLE_DEFS: RoleDef[] = [
  {
    id: "scene_perf",
    labelKey: "wizard.scenePerf",
    namePrefixKey: "home.namePrefixScenePerf",
    defaultMcpUrl: "http://127.0.0.1:3828/mcp",
    steps: ["connect", "prefs", "session"],
    siderWidth: 180,
    requiresMcp: true,
  },
  {
    id: "device_perf",
    labelKey: "wizard.devicePerf",
    namePrefixKey: "home.namePrefixDevicePerf",
    defaultMcpUrl: "http://127.0.0.1:3828/mcp",
    steps: ["session"],
    siderWidth: 220,
    requiresMcp: false,
  },
];

const _BY_ID = new Map(ROLE_DEFS.map((r) => [r.id, r]));

export function roleDef(id: string | null | undefined): RoleDef {
  return _BY_ID.get(id || "") || ROLE_DEFS[0];
}

/** 当前步骤的下一步。步骤表由扮演声明，页面不按角色名分支。 */
export function nextStep(
  roleId: string,
  current: "assistant" | "connect" | "prefs",
): SetupStep {
  const trail = ["assistant", ...roleDef(roleId).steps];
  const i = trail.indexOf(current);
  const nxt = trail[i + 1];
  return (nxt as SetupStep) || "session";
}

export function stepTrail(roleId: string): Array<"assistant" | SetupStep> {
  return ["assistant", ...roleDef(roleId).steps];
}
