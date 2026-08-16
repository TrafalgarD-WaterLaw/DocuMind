/** 公共标签映射（L: 此前 PATH_LABELS/REL_LABELS 在 4 个组件重复定义，
 * 且 KnowledgeGraphPanel 的"属于窑口/所属窑口"措辞还不一致——统一收口） */

/** 检索路径 → 中文标签（证据链/流水线/诊断共用，完整名） */
export const PATH_LABELS: Record<string, string> = {
  semantic: '语义',
  question: '假设问题',
  bm25: '关键词',
  graph: '图谱锚定',
  entity: '实体锚定',
  clip: '文找图',
}

/** 图谱关系类型 → 中文标签 */
export const REL_LABELS: Record<string, string> = {
  BELONGS_TO: '属于',
  EXCAVATED_AT: '出土于',
  BELONGS_TO_KILN: '属于窑口',
}
