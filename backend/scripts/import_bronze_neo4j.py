# -*- coding: utf-8 -*-
"""Import bronze vessel data to Neo4j knowledge graph

Build (Artifact)-[EXCAVATED_AT]->(Site) and (Artifact)-[BELONGS_TO]->(Era) graph.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.di import container  # noqa: E402

# ── 数据路径 ──
BRONZE_DIR = Path(r"E:/桌面/软创赛/datasets/青铜器/complete_DATASET")
COL_NAMES = ["序号", "图片编号", "器物名称", "时期", "尺寸", "类别", "出土地", "出土时间"]


def load_bronze_data() -> pd.DataFrame:
    """合并 train/test/val 三张表"""
    frames = []
    for name in ["train", "test", "val"]:
        fpath = BRONZE_DIR / f"{name}.xlsx"
        df = pd.read_excel(fpath)
        df.columns = COL_NAMES[: len(df.columns)]
        df["数据来源"] = name
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)

    # 清洗
    df["器物名称"] = df["器物名称"].fillna("未知器物")
    df["出土地"] = df["出土地"].fillna("不详")
    df["出土时间"] = df["出土时间"].fillna("不详")
    df["时期"] = df["时期"].fillna(0).astype(int)
    df["类别"] = df["类别"].fillna(0).astype(int)

    # 时期映射（数字 → 文字）
    era_map = {
        1: "商代", 2: "西周", 3: "春秋", 4: "战国",
        5: "秦代", 6: "汉代", 7: "魏晋", 8: "南北朝",
        9: "隋代", 10: "唐代", 11: "宋代", 12: "元代",
        13: "明代", 14: "清代", 15: "民国", 16: "近现代",
        17: "新石器时代", 18: "夏代",
    }
    df["时期名称"] = df["时期"].map(era_map).fillna("未知")

    return df


def build_cypher_statements(df: pd.DataFrame) -> list[str]:
    """生成 Cypher 语句"""
    stmts = []

    # 确保索引
    stmts.append("CREATE INDEX IF NOT EXISTS FOR (a:Artifact) ON (a.name)")
    stmts.append("CREATE INDEX IF NOT EXISTS FOR (s:Site) ON (s.name)")
    stmts.append("CREATE INDEX IF NOT EXISTS FOR (e:Era) ON (e.name)")

    seen_artifacts = set()
    seen_sites = set()
    seen_eras = set()

    for _, row in df.iterrows():
        name = str(row["器物名称"])
        site = str(row["出土地"])
        era = str(row["时期名称"])
        size = str(row.get("尺寸", ""))
        time = str(row.get("出土时间", ""))

        # 创建器物节点（仅首次）
        if name not in seen_artifacts:
            seen_artifacts.add(name)
            desc = f"尺寸:{size}" if size and size != "-" else ""
            stmts.append(
                f'MERGE (a:Artifact {{name: "{_escape(name)}"}}) '
                f'SET a.size = "{_escape(size)}", '
                f'a.excavation_time = "{_escape(time)}", '
                f'a.category = {int(row["类别"])}'
            )

        # 出土地节点
        if site not in seen_sites and site != "不详":
            seen_sites.add(site)
            stmts.append(f'MERGE (s:Site {{name: "{_escape(site)}"}})')

        # 时期节点
        if era not in seen_eras and era != "未知":
            seen_eras.add(era)
            stmts.append(f'MERGE (e:Era {{name: "{_escape(era)}"}})')

        # 关系
        if site != "不详":
            stmts.append(
                f'MATCH (a:Artifact {{name: "{_escape(name)}"}}), '
                f'(s:Site {{name: "{_escape(site)}"}}) '
                f'MERGE (a)-[:EXCAVATED_AT]->(s)'
            )

        if era != "未知":
            stmts.append(
                f'MATCH (a:Artifact {{name: "{_escape(name)}"}}), '
                f'(e:Era {{name: "{_escape(era)}"}}) '
                f'MERGE (a)-[:BELONGS_TO]->(e)'
            )

    return stmts


def _escape(s: str) -> str:
    return s.replace('"', '\\"').replace("'", "\\'")


def main():
    print("=== 导入青铜器数据到 Neo4j ===\n")

    g = container.graph
    if g is None:
        print("❌ Neo4j 不可用，请先启动 Neo4j 数据库")
        return

    # 1. 加载数据
    df = load_bronze_data()
    print(f"加载: {len(df)} 条青铜器记录")
    print(f"  器物: {df['器物名称'].nunique()} 种")
    print(f"  出土地: {df['出土地'][df['出土地']!='不详'].nunique()} 处")
    print(f"  时期: {df['时期名称'].nunique()} 个")

    # 2. 生成 Cypher
    stmts = build_cypher_statements(df)
    print(f"\nCypher 语句: {len(stmts)} 条")
    print(f"  节点: 索引 3 + 器物/地点/时期")
    print(f"  关系: EXCAVATED_AT + BELONGS_TO")

    # 3. 只执行索引（批量更高效，但从 Python 逐个执行也可以）
    # Neo4j Python driver 批量执行
    import neo4j
    driver = g.driver

    try:
        with driver.session() as session:
            for i, stmt in enumerate(stmts):
                try:
                    session.run(stmt)
                except Exception as e:
                    print(f"  [WARN] stmt {i}: {e}")

                if (i + 1) % 500 == 0:
                    print(f"  进度 {i + 1}/{len(stmts)}")
            print(f"\n完成! 执行了 {len(stmts)} 条语句")
    finally:
        driver.close()
        print("Neo4j 连接已关闭")


if __name__ == "__main__":
    main()
