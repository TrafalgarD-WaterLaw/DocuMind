# -*- coding: utf-8 -*-
"""多模态域服务包——CLIP 双塔 / 图片映射表 / VLM 图注 / 图片证据链 / 资产门面

image_caption 平铺在 services/ 下,CLIP 证据链混在 agent/quick.py 编排里,
图片资产同步靠各处自觉）。本包:

- clip_retrieval: CLIP 图文互检（文找图 / 图找图，clip_images collection）
- image_index:    source → 图片 URL 映射表（image_index.json）
- image_caption:  文档图片 VLM 描述（QwenVL / Noop 可插拔）
- evidence:       视觉命中 → 图注块证据链（独立于 RRF 排序，从 quick.py 抽出）
- assets:         图片资产门面（映射表 + CLIP 索引的注册/删除唯一入口）

检索侧的图片直检/clip 路仍在 retrieval/hybrid.py——那是六路召回的一部分，
属检索逻辑而非多模态资产管理。
"""
