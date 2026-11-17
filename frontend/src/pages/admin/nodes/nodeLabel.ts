// P138 4b — shared by the Nodes page and its dialog sections.
import type { NodeConfig } from "@/services/api";

export const DIRECT_TYPE = "direct";

/** A node's name as people know it; the KTSB Direct node gets its fixed name when it has no description. */
export function nodeLabel(id: string, n: NodeConfig, t: (k: string) => string): string {
  return n.description || (n.type === DIRECT_TYPE ? t("nodes.direct.name") : id);
}
