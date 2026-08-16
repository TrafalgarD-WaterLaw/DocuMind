/** ECharts graph node */
export interface GraphNode {
  id: string
  name: string
  category: string
  symbolSize: number
  /** 节点形状（朝代圆/窑口方/遗址三角/器物点） */
  symbol?: string
  /** 布局初始坐标（位置快照回填，保持重建不散架） */
  x?: number
  y?: number
  itemStyle?: {
    color?: string
    borderWidth?: number
    borderColor?: string
  }
  label?: {
    show?: boolean
    fontSize?: number
    color?: string
    position?: 'top' | 'bottom' | 'left' | 'right' | 'inside' | [number, number]
    fontWeight?: number
  }
  /** tooltip 附件字段 */
  introduce?: string
  when?: string
  where?: string
}

/** ECharts graph link */
export interface GraphLink {
  source: string
  target: string
  label?: {
    show?: boolean
    formatter?: string
  }
  lineStyle?: {
    width?: number
    curveness?: number
  }
}
