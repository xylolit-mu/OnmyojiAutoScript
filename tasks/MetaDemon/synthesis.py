"""合成标题解析：标题星级是产物，配置星级是材料。"""
import re


def parse_synthesis_material_level(text: str) -> int | None:
    match = re.fullmatch(r'合成([二三四五六])星裂变之眼', re.sub(r'\s+', '', text or ''))
    if match is None:
        return None
    return {'二': 1, '三': 2, '四': 3, '五': 4, '六': 5}[match.group(1)]
