# -*- coding: utf-8 -*-
"""P1-C 数据契约校验——source 命名规范（领域规则,纯函数无 IO）


三种合法 source 形态:
  1. {域}-{实体}            文本块，如 青铜-叩鼎 / 河南博物院-妇好墓玉龙 / 宣德-青花梅瓶
  2. {域}-{实体}#图         图片块（#图 后缀，隔离图片来源多样性），如 河南博物院-妇好墓玉龙#图
  3. {timestamp}_{file}    上传文档（天然时间戳前缀），如 1234567890_a.pdf / 1786074778_妇好鸮尊.pdf
规则说明:
  - 域与实体均须含至少一个汉字（"Bronze-ding" 这类纯拉丁词不合法）
  - 域/实体不含空格、连字符与 #（"青铜 叩鼎" / "青铜--叩鼎" 不合法）
"""
from __future__ import annotations

import re

_CJK = "一-鿿"
# 域/实体片段: 不含空格/连字符/#，且至少含一个汉字
_PART = rf"(?=[^\-\s#]*[{_CJK}])[^\-\s#]+"
# 形态一: {域}-{实体}
_DOMAIN_ENTITY = re.compile(rf"^{_PART}-{_PART}$")
# 形态二: {域}-{实体}#图
_DOMAIN_ENTITY_IMAGE = re.compile(rf"^{_PART}-{_PART}#图$")
# 形态三: {timestamp}_{file}（timestamp 为数字前缀，file 非空）
_TIMESTAMP_FILE = re.compile(r"^\d{1,20}_\S+$")


def validate_source(source: str) -> bool:
    """P1-C 数据契约校验——source 命名是否合法

    合法: {域}-{实体} / {域}-{实体}#图 / {timestamp}_{file}（正则实现，见模块注释）
    非法: 空串、无连字符/下划线结构、域或实体缺失或含空格、纯拉丁词（如 Bronze-ding）等
    """
    if not isinstance(source, str) or not source:
        return False
    return bool(
        _DOMAIN_ENTITY.match(source)
        or _DOMAIN_ENTITY_IMAGE.match(source)
        or _TIMESTAMP_FILE.match(source)
    )
