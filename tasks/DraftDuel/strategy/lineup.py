"""从《协同分析.xlsx》生成的资料中选择式神和推荐搭配。

本模块不依赖游戏画面。调用方传入当前可选式神及已选队伍，
仅在 OCR 能可靠识别名称后才调用 choose_pick。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


try:
    from tasks.DraftDuel.strategy import battle_stats
except Exception:  # 离线单文件测试时允许缺统计模块
    battle_stats = None

CATALOG_PATH = Path(__file__).resolve().parents[1] / 'catalog.json'
ENCYCLOPEDIA_PATH = Path(__file__).resolve().parents[1] / 'encyclopedia.json'
STRENGTH_PATH = Path(__file__).resolve().parents[1] / 'strength_scores.json'
TIER_POINTS = {'特等': 40, '上等': 30, '中等': 20, '下等': 10}
ENCYCLOPEDIA_POINTS = {'SS': 12, 'S': 8, 'A': 4, 'B': 0,
                       'C': -4, 'D': -8, 'E': -12}
USER_RATINGS = {
    '梦山白藏主': 'S',
    '灼华桃花妖': 'S',
    '蚀月吸血姬': 'S',
    '天火命铃彦姬': 'S',
    # 用户钦定（2026-10-28）：这些卡在百科里要么样本极少要么评级偏低, 按强卡处理
    '卑弥呼': 'S',
    '平将门': 'S',
    '云间不见岳': 'S',
    # 用户追加（2026-10-28）：莹草/化鲸已加强、SSR鬼童丸也是强单卡, 百科胜率是旧数据
    '莹草': 'S',
    '化鲸': 'S',
    '鬼童丸': 'S',
    # 用户追加（2026-10-29）：茨木童子也加强了, 输出评分要抬上来
    '茨木童子': 'S',
}
USER_TIERS = {'莹草': '特等', '云间不见岳': '特等',
              '卑弥呼': '特等', '平将门': '特等',
              # 六番阵容里各出现 4 套的常驻火机/辅助（百科无样本, 按实战略高定档）
              '思金神': '特等', '神无月': '特等',
              '鬼童丸': '特等', '茨木童子': '特等'}
SUMMON_SLOT_USERS = frozenset({'猫川', '千姬', '雪御前', '跳跳妹妹', '惠比寿'})
USER_ROLES = {
    '梦山白藏主': {'support'},
    '灼华桃花妖': {'sustain'},
    '蚀月吸血姬': {'sustain', 'damage'},
    '天火命铃彦姬': {'damage'},
    # 用户口径(2026-10-30): 初音未来不是鬼火, 是加速+增伤(带一丝丝盾)
    '初音未来': {'support', 'tempo'},
    # 百科/官方口径：卑弥呼是"拉条+增伤+回合外行动"的多功能辅助（119 初始速度），
    # 不是主输出；协同里她负责拉条/增伤，输出交给茨林、SP泷夜叉姬、平将门等。
    '卑弥呼': {'support', 'tempo'},
    # 木之本樱：被动"幻影"控制 + 全队无视防御，控制兼增伤。
    '木之本樱': {'control', 'support'},
    # 莹草：反击伤害加强后是"奶 + 输出"混合位, 不能只当纯奶统计。
    '莹草': {'sustain', 'damage'},
    # SP惠比寿（晨辉惠比寿）：推条 + 回血 + 回火，用户认定是优质辅助卡，
    # 三个位（打火/治疗/拉条）都能顶。
    '晨辉惠比寿': {'fire', 'sustain', 'tempo'},
}
ROLE_POINTS = {'fire': 12, 'sustain': 10, 'shield': 7, 'control': 6,
               'damage': 8, 'tempo': 6, 'support': 4}
# 2026-10-28 选人页实测的整名误认（描边字体 + 漏字），逐条对应图鉴规范名。
# 这些名字无法靠单字替换或近形相似度唯一还原，只能整名映射。
OCR_NAME_ALIASES = {
    # 2026-10-31 MUMU1 选人实测：首字误读且末尾“刃”漏识别。
    '天刀姬绯夜猎': '妖刀姬·绯夜猎刃',
    '久次食良': '久次良',
    '书羽': '书翁',
    '其佣': '兵俑',
    '夜又': '夜叉',
    '孟婴': '孟婆',
    '巷风一百连': '苍风一目连',
    '一百连': '一目连',
    '曰藏王': '白藏主',
    '梦山曰藏王': '梦山白藏主',
    '炼狱茨术量子': '炼狱茨木童子',
    '神心会外镜': '禅心云外镜',
    '蛋草': '莹草',
    '酒谷量子': '酒吞童子',
    '饭司': '饭笥',
    '饭筒': '饭笥',
    '鲸沙干姬': '鲸汐千姬',
    '鸠': '鸩',
    '李': '李小狼',
    '权图': '椒图',
    '泥夜文姬': '泷夜叉姬',
    # 2026-10-29 选人页实测
    '神塑八岐天蛇': '神堕八岐大蛇',
    '神塑八岐大蛇': '神堕八岐大蛇',
    '大夜摩大阎魔': '大夜摩天阎魔',
    '柄洋种': '祸津神',
    '鲸炒干姬': '鲸汐千姬',
    '披岸化': '彼岸花',
    # 用户确认：心发天神 = 心友犬神
    '心发天神': '心友犬神',
}
# 描边字体下 OCR 的常见近形误认; 仅在纠正后能命中图鉴时才采用
OCR_CHAR_FIX = {
    '乌': '鸟', '蛛': '蝶', '巻': '卷', '稱': '祢', '煉': '炼',
    '姫': '姬', '鈴': '铃', '鳥': '鸟', '龍': '龙', '鳳': '凤',
    '剣': '剑', '歩': '步', '壱': '壹', '畫': '画', '黃': '黄',
    '黒': '黑', '澤': '泽', '実': '实', '涼': '凉', '鍊': '炼',
    # 实测 2026-10-28: 弈→升, 帝释天→带释天
    '升': '弈', '带': '帝',
    # 实测 2026-10-28 20:31 选人: 茨木童子→次木量子, 待宵姑获鸟→待肖姑获马
    '次': '茨', '量': '童', '术': '木', '肖': '宵', '马': '鸟',
}
_OCR_FIX_TABLE = str.maketrans(OCR_CHAR_FIX)
SOUL_NAMES = (
    '伤魂鸟', '轮入道', '青女房', '返魂香', '雪幽魂', '钓瓶火',
    '遗念火', '招财', '火灵', '蚌精', '木魅', '薙魂', '地藏',
    '针女', '破势', '狂骨', '网切', '树妖', '珍珠', '狰',
    '魅妖', '魍魉', '钟灵', '日女', '共潜', '共浅', '镜姬',
    '贝吹坊', '出世螺', '隐念', '海月', '三味', '镇墓兽',
)

# 编排公式（用户口径，6 人合计）：2 打火 + 2 输出 + 1 治疗/盾 + 1 控制或拉条。
# 强力增伤可以顶掉后两个辅助位；评分与上阵都按这个目标算。
FORMULA_ROLES = {'fire': 2, 'damage': 2, 'protect': 1, 'flex': 1}
# 用户口径(2026-10-30): 队伍里至少要 2 个打火, 一手输出一手火不够用。
FIRE_TARGET = 2
# 真正能回火的打火机（用户口径 2026-10-30: 薰也是真火, 能自解控+帮队友驱散还打火）
CORE_FIRE = frozenset({'座敷童子', '辉夜姬', '因幡辉夜姬', '追月神', '流光追月神',
                       '千姬', '鲸汐千姬', '天井下', '薰'})
# 次等打火(有一定回火, 够用但必须配一个真火; 用户口径: sp座敷也算这一档)
SECONDARY_FIRE = frozenset({'鬼金羊', '青行灯', '初音未来', '晨辉惠比寿', '福悦座敷童子'})
# 用户口径(2026-10-30): 这几张不靠鬼火也有强度, 组不到火时凑成"无火队"反而能打
NO_FIRE_CORE = frozenset({'莹草', '化鲸', '荒川之主', '大夜摩天阎魔', '鬼童丸'})
# 用户钦定的高优先级打火: 薰(自解控 + 帮队友驱散 + 回火), 缺火时优先于其它打火机
PRIORITY_FIRE = frozenset({'薰'})
# 用户钦定的强单卡（2026-10-28）：百科样本少或胜率一般也算核心, 不参与 <45% 硬过滤。
USER_STRONG_CARDS = frozenset({'蚀月吸血姬', '莹草', '化鲸', '鬼童丸'})
# 用户钦定的强输出核心：遇到就优先拿, 不受百科胜率门槛限制。
USER_STRONG_DPS = frozenset({'蚀月吸血姬', '平将门', '季', '本真三尾狐',
                             '化鲸', '鬼童丸', '黑童子', '茨木童子'})
# 用户钦定的强组合（名单 + 加分）；能打出六番胜的阵容按强力处理, 不能当弱卡。
USER_COMBOS = (
    (frozenset({'流光追月神', '季', '本真三尾狐'}), 30),
    (frozenset({'流光追月神', '季', '木之本樱'}), 30),
    (frozenset({'流光追月神', '季'}), 16),
    (frozenset({'天照', '黑童子'}), 20),
    (frozenset({'平将门', '云间不见岳'}), 20),
    (frozenset({'纺愿缘结神', '食发鬼'}), 20),
    (frozenset({'鬼王酒吞童子', '禅心云外镜'}), 16),
    # 用户口径(2026-10-30) 强力组合:
    # 初音未来 必须配 sp千姬(鲸汐千姬), 一起上场很强(加速+增伤+一点点盾)
    (frozenset({'初音未来', '鲸汐千姬'}), 22),
    # 不知火 + 酒吞 / sp红叶(心狩鬼女红叶)
    (frozenset({'不知火', '酒吞童子'}), 12),
    (frozenset({'不知火', '心狩鬼女红叶'}), 12),
    # 猫川 + 夏目 / 兵俑, 三者同上再加一点
    (frozenset({'猫川', '夏目猫老师'}), 14),
    (frozenset({'猫川', '兵俑'}), 12),
    (frozenset({'猫川', '夏目猫老师', '兵俑'}), 8),
    # 椒图 + 莹草(两个都算保护, 剩下的人不能再选保护)
    (frozenset({'椒图', '莹草'}), 12),
    # 季 + sp三尾狐(本真三尾狐)
    (frozenset({'季', '本真三尾狐'}), 12),
    # 椒图 / 天照 / 鬼吞 + 小小黑(黑童子)
    (frozenset({'椒图', '黑童子'}), 12),
    (frozenset({'鬼王酒吞童子', '黑童子'}), 12),
    # 云间不见岳 + 平将门/铃彦姬/sp铃彦姬(天火命铃彦姬)/sp座敷(福悦座敷童子)/摩托(大夜摩天阎魔)
    (frozenset({'云间不见岳', '铃彦姬'}), 12),
    (frozenset({'云间不见岳', '天火命铃彦姬'}), 12),
    (frozenset({'云间不见岳', '福悦座敷童子'}), 12),
    (frozenset({'云间不见岳', '大夜摩天阎魔'}), 12),
    # 思金神 + 平将门 / sp铃彦姬
    (frozenset({'思金神', '平将门'}), 12),
    (frozenset({'思金神', '天火命铃彦姬'}), 12),
) + tuple(
    (frozenset({'卑弥呼', partner}), 14)
    for partner in ('炼狱茨木童子', '时曜泷夜叉姬', '平将门',
                    '玉藻前', '烬天玉藻前', '荒川之主')
)
# 强增伤：样本少但用户指定是组合核心（SP不见岳）。
USER_STRONG_SUPPORT = frozenset({'云间不见岳'})
# 用户发来的 12 套实战六番胜阵容（2026-10-28，昵称已按图鉴规范名落库）。
# 昵称对照：sp茶几=时曜泷夜叉姬、sp老头=晨辉惠比寿、老头=惠比寿、小小黑=黑童子、
# p岳/sp不见岳=云间不见岳、sp追=流光追月神、sp桃=灼华桃花妖、sp千姬=鲸汐千姬、
# 美=伊邪那美、离=不知火、平=平将门、因幡=因幡辉夜姬、熏=薰。
SIX_WIN_LINEUPS = (
    frozenset({'化鲸', '天井下', '大天狗', '思金神', '卑弥呼', '因幡辉夜姬'}),
    frozenset({'天火命铃彦姬', '遥念烟烟罗', '日和坊', '思金神', '云间不见岳', '辉夜姬'}),
    frozenset({'莹草', '鬼切', '鲸汐千姬', '追月神', '帝释天', '玉藻前'}),
    frozenset({'思金神', '铃彦姬', '伊邪那美', '神无月', '玉藻前', '云间不见岳'}),
    frozenset({'因幡辉夜姬', '时曜泷夜叉姬', '卑弥呼', '晨辉惠比寿', '鬼金羊', '食发鬼'}),
    frozenset({'薰', '惠比寿', '食发鬼', '神无月', '修罗鬼童丸', '迦楼罗'}),
    frozenset({'蚀月吸血姬', '神无月', '黑童子', '化鲸', '椒图', '不见岳'}),
    frozenset({'茨木童子', '孟婆', '薰', '卑弥呼', '鬼金羊', '神无月'}),
    frozenset({'玉藻前', '云间不见岳', '卑弥呼', '因幡辉夜姬', '孟婆', '日和坊'}),
    frozenset({'鲤鱼精', '灼华桃花妖', '黑童子', '薰', '天照', '流光追月神'}),
    frozenset({'辉夜姬', '言灵', '平将门', '云间不见岳', '思金神', '不知火'}),
    frozenset({'伊邪那美', '莹草', '化鲸', '因幡辉夜姬', '食发鬼', '辉夜姬'}),
)
SIX_WIN_FREQUENCY = Counter(name for lineup in SIX_WIN_LINEUPS for name in lineup)
# 命中白名单阵容的重合人数 -> 加分（3 人起算，6 人全中给满）
SIX_WIN_OVERLAP_POINTS = {3: 8, 4: 18, 5: 32, 6: 45}
# 各类型推荐式神（catalog 规范名；首选在前）。
# 治疗推荐按用户公式：SP桃花妖（灼华桃花妖）、萤草（百科名“莹草”，R卡治疗）、日和坊。
ROLE_RECOMMEND = {
    'fire': ('流光追月神', '薰', '因幡辉夜姬', '神无月', '千姬'),
    'sustain': ('灼华桃花妖', '莹草', '日和坊', '情思日和坊'),
    'control': ('百目鬼', '小松丸'),
    'shield': ('鲤鱼精', '一目连'),
    'damage': ('蚀月吸血姬', '平将门', '时曜泷夜叉姬',
               '炼狱茨木童子', '茨木童子'),
}
RECOMMEND_BONUS = 8      # 首选式神额外加分
RECOMMEND_BONUS_SECOND = 4  # 次选式神额外加分


@dataclass(frozen=True)
class Entry:
    name: str
    rarity: str
    role: str
    strength: str
    weakness: str
    notes: str
    souls: str
    tier: str
    source_row: int


@dataclass(frozen=True)
class Pick:
    name: str
    score: float
    reasons: tuple[str, ...]
    suggested_souls: tuple[str, ...]


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Entry]:
    data = json.loads(path.read_text(encoding='utf-8'))
    return {row['name']: Entry(**row) for row in data['entries']}


CATALOG = load_catalog()


def load_strength_scores(path: Path = STRENGTH_PATH) -> tuple[dict, tuple]:
    if not path.exists():
        return {}, ()
    data = json.loads(path.read_text(encoding='utf-8'))
    return data['scores'], tuple(data['combinations'])


STRENGTH_SCORES, COMBINATION_RECOMMENDATIONS = load_strength_scores()


def load_encyclopedia(path: Path = ENCYCLOPEDIA_PATH) -> tuple[dict, dict, dict]:
    if not path.exists():
        return {}, {}, {}
    rows = json.loads(path.read_text(encoding='utf-8'))['entries']
    grouped = {}
    aliases = {}
    for row in rows:
        canonical = row['canonical_name']
        grouped.setdefault(canonical, []).append(row)
        aliases[re.sub(r'[\s·•・&＆:：【】\[\]（）()]', '', row['name'])] = canonical
    # 云外镜有黑、白两行；用样本量较多的一行作评分依据，定位则可合并。
    primary = {
        name: max(items, key=lambda item: (
            (item['wins'] or 0) + (item['losses'] or 0),
            ENCYCLOPEDIA_POINTS.get(item['rating'], -99)))
        for name, items in grouped.items()
    }
    return primary, aliases, grouped


ENCYCLOPEDIA, NAME_ALIASES, ENCYCLOPEDIA_VARIANTS = load_encyclopedia()


def _name_text(name: str) -> str:
    # 间隔点是名字内部标点，只在比较时忽略，绝不据此拆分式神。
    return re.sub(r'[\s·•・&＆:：【】\[\]（）()]', '', name or '')


def _levenshtein_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def normalize_name(name: str, catalog: Mapping[str, Entry] = CATALOG) -> str | None:
    """短名只接受精确匹配；长名使用唯一的莱文斯坦近似结果。"""
    if '?' in (name or '') or '？' in (name or ''):
        return None
    value = _name_text(name)
    value = re.sub(r'^(?:SSR|SP|SR|UR|R|联动)', '', value, flags=re.I)
    canonical = {_name_text(candidate): candidate for candidate in catalog}
    if value in canonical:
        return canonical[value]
    if len(value) < 3:
        return None
    value = value.replace('灶门称豆子', '灶门祢豆子')
    for aliases in (NAME_ALIASES, OCR_NAME_ALIASES):
        target = aliases.get(value)
        if target in catalog and len(_name_text(target)) >= 3:
            return target
    fixed = value.translate(_OCR_FIX_TABLE)
    if fixed in canonical and len(fixed) >= 3:
        return canonical[fixed]
    # 三至五字允许一个编辑，六字及以上最多两个；插入、删除、替换都计一次。
    ranked = []
    for text, candidate in canonical.items():
        if len(text) < 3:
            continue
        limit = 1 if min(len(value), len(text)) < 6 else 2
        if abs(len(text) - len(value)) > limit:
            continue
        distance = _levenshtein_distance(value, text)
        ratio = 1 - distance / max(len(value), len(text))
        if distance <= limit and ratio >= 2 / 3 - 1e-9:
            ranked.append((ratio, candidate))
    if ranked:
        ranked.sort(reverse=True)
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-9:
            return None
        return ranked[0][1]
    # 带间隔点的整名不能因近似失败而退回到点前的基础式神。
    if any(mark in (name or '') for mark in ('·', '•', '・')):
        return None
    # 仅保留完整长名被少量属性文字包围的情况；短名不通过子串猜测。
    matches = [candidate for text, candidate in canonical.items()
               if len(text) >= 3 and text in value]
    if not matches:
        return None
    length = max(len(_name_text(candidate)) for candidate in matches)
    longest = [candidate for candidate in matches if len(_name_text(candidate)) == length]
    return longest[0] if len(longest) == 1 else None


def role_tags(entry: Entry, *, defense: int | None = None) -> set[str]:
    if entry.name in USER_ROLES:
        return set(USER_ROLES[entry.name])
    if entry.name == '云外镜':
        # 白镜侧重驱散与拉条；防御面板不明时按黑镜保守估值。
        return {'support', 'tempo'} if defense is not None and defense >= 900 else {'damage'}
    role = entry.role
    tags = set()
    if '火机' in role or '鬼火' in role:
        tags.add('fire')
    if any(word in role for word in ('奶', '治疗', '复活')):
        tags.add('sustain')
    if any(word in role for word in ('盾', '减伤', '保单核', '抗单体')):
        tags.add('shield')
    if any(word in role for word in ('控制', '推条')):
        tags.add('control')
    if '输出' in role or '增伤' in role:
        tags.add('damage' if '输出' in role else 'support')
    if '拉条' in role:
        tags.add('tempo')
    if '辅助' in role or '驱散' in role or '解控' in role:
        tags.add('support')
    extra = ENCYCLOPEDIA.get(entry.name, {}).get('role', '')
    if extra == '打火':
        tags.add('fire')
    elif extra == '治疗':
        tags.add('sustain')
    elif extra == '护卫':
        tags.add('shield')
    elif extra == '控制':
        tags.add('control')
    elif extra == '输出':
        tags.add('damage')
    elif extra == '拉条':
        tags.add('tempo')
    elif extra == '综合':
        tags.add('support')
    return tags


def is_fire_supplier(name: str, catalog: Mapping[str, Entry] = CATALOG) -> bool:
    entry = catalog.get(name)
    return entry is not None and 'fire' in role_tags(entry)


def encyclopedia_points(name: str, *, team: Iterable[str] = (),
                        defense: int | None = None) -> float:
    """百科评级和胜率是辅助信号；小样本胜率向 50% 收缩。"""
    row = ENCYCLOPEDIA.get(name)
    if name == '云外镜':
        variant = '云外镜（白）' if defense is not None and defense >= 900 else '云外镜（黑）'
        row = next((item for item in ENCYCLOPEDIA_VARIANTS.get(name, ())
                    if item['name'] == variant), row)
    if not row:
        return 0.0
    samples = (row['wins'] or 0) + (row['losses'] or 0)
    rating = row['rating']
    if name in USER_RATINGS:
        rating = USER_RATINGS[name]
    if name == '茨木童子':
        rating = 'S' if '云间不见岳' in team else 'A'
    grade = ENCYCLOPEDIA_POINTS.get(rating, 0)
    user_rating = name in USER_RATINGS or name == '茨木童子'
    grade_weight = 1.0 if user_rating else min(1.0, max(0.5, samples / 300)) if samples else 0.5
    shrunk_winrate = ((row['wins'] or 0) + 50) / (samples + 100)
    # 百科胜率是唯一的大样本实战信号（单卡 100-1000 场），权重需能压过稀有度直觉。
    winrate_points = max(-14, min(14, (shrunk_winrate - 0.5) * 56)) if samples else 0
    return grade * grade_weight + winrate_points


def tier_points(entry: Entry, team: Iterable[str] = (), *, speed: int | None = None,
                defense: int | None = None) -> int:
    """条件未满足时取保守等级；未知等级不凭稀有度加分。"""
    if entry.name in USER_TIERS:
        return TIER_POINTS[USER_TIERS[entry.name]]
    if entry.name == '梦山白藏主':
        return 40
    label = entry.tier
    if label in TIER_POINTS:
        return TIER_POINTS[label]
    if label in ('?', '？', ''):
        return 0
    names = set(team)
    if entry.name == '云外镜':
        return 40 if defense is not None and defense >= 900 else 20
    if entry.name == '茨木童子':
        return 40 if '云间不见岳' in names else 30
    if '220以上特等' in label and speed is not None:
        return 40 if speed >= 220 else 30 if speed >= 210 else 20
    if '220以上上等' in label and speed is not None:
        return 30 if speed >= 220 else 20 if speed >= 210 else 10
    if '170上等' in label and speed is not None:
        return 30 if speed >= 170 else 20
    if '火山队特等' in label or '火系特等' in label:
        return 40 if '火山队' in names else 30 if '上等' in label else 10
    if '有季特等' in label:
        return 40 if '季' in names else 30
    if 'SP千姬上等' in label:
        return 30 if '鲸汐千姬' in names else 20
    if '伤魂鸟上等' in label:
        return 30 if '伤魂鸟' in names else 20
    if '破势特等' in label:
        return 40 if '破势' in names else 30
    if '上等到特等之间' in label or '上等特等之间' in label:
        return 35
    if '单上中等' in label or '120速中等' in label:
        return 20
    if '中等' in label:
        return 20
    if '上等' in label:
        return 30
    if '特等' in label:
        return 40
    if '下等' in label:
        return 10
    return 0


def strength_points(entry: Entry, team: Iterable[str] = (), *, speed: int | None = None,
                    defense: int | None = None) -> float:
    """新表独立强度分；用户指定评级及条件形态保持优先。"""
    old = tier_points(entry, team, speed=speed, defense=defense)
    if entry.name in USER_TIERS or entry.name in USER_RATINGS or entry.name in (
            '云外镜', '茨木童子'):
        return float(old)
    score = STRENGTH_SCORES.get(entry.name, {}).get('strength')
    if not isinstance(score, (int, float)):
        return float(old)
    if entry.tier not in TIER_POINTS and entry.tier not in ('', '?', '？'):
        return float(old)
    points = ((60, 10), (74, 20), (85, 30), (94, 40))
    if score <= 60:
        return max(0.0, score / 6)
    for (low, low_points), (high, high_points) in zip(points, points[1:]):
        if score <= high:
            return low_points + (score - low) * (high_points - low_points) / (high - low)
    return min(45.0, 40.0 + (score - 94))


# 从“组合推荐”中提取可直接由式神名称判断的核心搭配。泛指“输出/火机”
# 由 role_tags 与队伍平衡评分处理，避免把说明文本当作可点选的式神。
COMBINATION_PAIRS = (
    ({'云间不见岳', '炼狱茨木童子'}, 9),
    ({'云间不见岳', '烬天玉藻前'}, 9),
    ({'鬼王酒吞童子', '禅心云外镜'}, 10),
    ({'浮世青行灯', '心狩鬼女红叶'}, 9),
    ({'食灵', '姑获鸟'}, 8),
    ({'食灵', '鬼童丸'}, 7),
    ({'食灵', '心友犬神'}, 7),
    ({'空相面灵气', '大夜摩天阎魔'}, 8),
    ({'龙珏', '歌留多'}, 10),
    ({'不知火', '酒吞童子'}, 8),
    ({'不知火', '吸血姬'}, 8),
    ({'鲸汐千姬', '初音未来'}, 9),
    ({'季', '犬神'}, 8),
    ({'一目连', '匣中少女'}, 7),
    ({'鲤鱼精', '匣中少女'}, 7),
    ({'蚀月吸血姬', '阎魔'}, 7),
    ({'神启荒', '大夜摩天阎魔'}, 7),
    ({'神启荒', '遥念烟烟罗'}, 7),
    ({'言灵', '鬼金羊'}, 9),
    ({'入内雀', '荒骷髅'}, 7),
)


def suggested_souls(entry: Entry) -> tuple[str, ...]:
    found = [(entry.souls.index(soul), soul) for soul in SOUL_NAMES
             if soul in entry.souls]
    result = [soul for _, soul in sorted(found)[:3]]
    extra = ENCYCLOPEDIA.get(entry.name)
    if extra and len(result) < 3:
        text = str(extra['summary']) + '\n' + str(extra['panel'])
        for soul in SOUL_NAMES:
            if soul in text and soul not in result:
                result.append(soul)
                if len(result) == 3:
                    break
    return tuple(result)


def _winrate_ok(name: str, floor: float = 0.5) -> bool:
    """弱输出(百科胜率明显低于 50%)不算进"2 输出"公式, 免得为凑数上 34% 胜率的输出。"""
    row = ENCYCLOPEDIA.get(name)
    if not row:
        return True
    samples = (row['wins'] or 0) + (row['losses'] or 0)
    if samples < 50:
        return True
    return ((row['wins'] or 0) + 50) / (samples + 100) >= floor


def _strong_support(name: str) -> bool:
    """强力增伤/辅助: 大样本胜率过半(如食灵 52.3%/1000 场)或者特等强度卡, 可以顶掉治疗/盾位或控制/拉条位。"""
    if name in USER_STRONG_SUPPORT:
        return True
    row = ENCYCLOPEDIA.get(name) or {}
    samples = (row.get('wins') or 0) + (row.get('losses') or 0)
    if samples >= 200:
        return (row.get('winrate') or 0) >= 0.52
    entry = CATALOG.get(name)
    if entry is None:
        return False
    strength = STRENGTH_SCORES.get(name, {}).get('strength')
    return entry.tier == '特等' and isinstance(strength, (int, float)) and strength >= 35


def _is_amplifier(name: str, catalog: Mapping[str, Entry] = CATALOG) -> bool:
    """标记为增伤的辅助(区别于驱散/解控这类纯辅助), 强增伤可以顶掉辅助位。"""
    entry = catalog.get(name)
    if entry is None:
        return False
    text = f"{entry.role}{ENCYCLOPEDIA.get(name, {}).get('role', '')}{entry.notes[:60]}"
    return '增伤' in text


def composition_counts(names: Iterable[str], catalog: Mapping[str, Entry] = CATALOG,
                       defenses: Mapping[str, int] | None = None) -> dict[str, int]:
    """按用户公式统计编排位：2 打火 + 2 输出 + 1 治疗/盾 + 1 控制或拉条。

    输出按角色标签计, 不看胜率(胜率低的输出只降评分, 不能让公式永远凑不齐);
    强力增伤算"万能辅助位", 同时顶治疗/盾位和控制/拉条位(protect/flex 各 +1)。
    """
    names = list(names)
    defens = defenses or {}
    tags = [role_tags(catalog[name], defense=defens.get(name)) for name in names]
    fire = sum('fire' in tag for tag in tags)
    damage = sum('damage' in tag for tag in tags)
    weak_damage = sum('damage' in tag and not _winrate_ok(name, 0.45)
                      for tag, name in zip(tags, names))
    protect = sum(bool({'sustain', 'shield'} & tag) for tag in tags)
    flex = sum(bool({'control', 'tempo'} & tag) for tag in tags)
    amplify = sum('support' in tag and 'damage' not in tag
                  and _is_amplifier(name, catalog) and _strong_support(name)
                  for tag, name in zip(tags, names))
    # 纯奶: 只有治疗/盾, 不带控制/拉条/增伤(用户口径: 两个辅助位尽量别全是奶)
    heal_only = sum(bool({'sustain', 'shield'} & tag)
                    and not ({'control', 'tempo', 'support', 'damage'} & tag)
                    for tag in tags)
    return {'fire': fire, 'damage': damage, 'weak_damage': weak_damage,
            'protect': protect + amplify, 'flex': flex + amplify,
            'amplify': amplify, 'heal_only': heal_only}


def team_score(names: Iterable[str], catalog: Mapping[str, Entry] = CATALOG,
               speeds: Mapping[str, int] | None = None,
               defenses: Mapping[str, int] | None = None) -> float:
    team = list(names)
    if len(team) != len(set(team)):
        raise ValueError('阵容中不能重复选择同名式神')
    unknown = [name for name in team if name not in catalog]
    if unknown:
        raise ValueError(f'未知式神: {unknown}')
    speeds = speeds or {}
    defenses = defenses or {}
    tags = [role_tags(catalog[name], defense=defenses.get(name)) for name in team]
    score = sum(strength_points(catalog[name], team, speed=speeds.get(name),
                            defense=defenses.get(name))
                # 百科胜率是大样本实战信号, 权重放大到 3 倍, 压过直觉/推荐名单
                + 3 * encyclopedia_points(name, team=team, defense=defenses.get(name))
                + ((STRENGTH_SCORES[name]['combo'] - 75) * 0.18
                   if name in STRENGTH_SCORES
                   and isinstance(STRENGTH_SCORES[name]['combo'], (int, float))
                   else 0)
                for name in team)
    for role, value in ROLE_POINTS.items():
        count = sum(role in item for item in tags)
        if count:
            score += value
            if role == 'damage' and count >= 2:
                score += 4
    # 编排公式（用户口径）：2 打火 + 2 输出 + 1 治疗/盾 + 1 控制或拉条,
    # 强力增伤可以顶掉后两个辅助位；无火/无输出重罚, 超编递减。
    counts = composition_counts(team, catalog, defenses)
    fire_count = counts['fire']
    damage_count = counts['damage']
    score += 18 * min(fire_count, 2) - 16 * max(0, fire_count - 2)
    if fire_count == 0 and len(team) >= 2:
        score -= 26
    # 用户口径(2026-10-30): 无火队若由这几张(不靠鬼火也有输出/续航)组成, 抵消无火重罚
    no_fire_core = len(NO_FIRE_CORE.intersection(team))
    if fire_count == 0 and no_fire_core >= 2:
        score += 9.0 * min(no_fire_core, 4)
    score += 10 * min(damage_count, 2) + (14 if damage_count >= 2 else 0)
    score -= 8 * max(0, damage_count - 2)
    if damage_count == 0 and len(team) >= 2:
        score -= 22
    score -= 6 * counts['weak_damage']
    if counts['protect']:
        score += 10
    elif len(team) >= 4:
        score -= 14
    # 用户口径(2026-10-30): 椒图+莹草 两个保护位已满, 其他人再带保护要扣分
    if {'椒图', '莹草'} <= set(team) and counts['protect'] > 2:
        score -= 18 * (counts['protect'] - 2)
    if counts['flex']:
        score += 8
    elif len(team) >= 4:
        score -= 12
    score -= 20 * max(0, len(SUMMON_SLOT_USERS.intersection(team)) - 1)
    # 表中明确写出的正向搭配。仅命中实际同队名称才加分。
    pairs = (
        ({'初音未来', '鲸汐千姬'}, 9),
        ({'桃花妖', '季'}, 8),
        ({'桃花妖', '云外镜'}, 8),
        ({'桃花妖', '镜音铃连'}, 8),
        ({'稻荷神御馔津', '纺愿缘结神'}, 8),
        ({'犬神', '季'}, 8),
        # 用户口径(2026-10-30): 夏目猫老师 和 猫川 同队互相加分
        ({'夏目猫老师', '猫川'}, 14),
    )
    pair_scores = {frozenset(pair): points for pair, points in pairs}
    for pair, points in COMBINATION_PAIRS:
        pair_scores[frozenset(pair)] = points
    # 用户钦定的强组合（SP追月神+季+三尾狐/小樱、平将门+SP不见岳、SP缘结神+食发鬼、
    # 卑弥呼+X、鬼吞+禅镜等）：能六番的阵容按强力处理。
    for combo, points in USER_COMBOS:
        pair_scores[combo] = points
    score += sum(points for pair, points in pair_scores.items() if pair <= set(team))
    # 六番胜白名单：与实战六番阵容重合 3 人起加分（只算重合最多的那套）
    overlap = max((len(lineup & set(team)) for lineup in SIX_WIN_LINEUPS), default=0)
    score += SIX_WIN_OVERLAP_POINTS.get(overlap, 0)
    # 常驻强卡（在多套六番阵容里反复出现）做小幅加权, 只当同分时的倾向
    score += sum(1.5 * min(2, SIX_WIN_FREQUENCY[name] - 1)
                 for name in team if SIX_WIN_FREQUENCY.get(name, 0) > 1)
    if {'帝释天', '纺愿缘结神'} <= set(team):
        score -= 8  # 组合表提示两名单拉重复。
    return float(score)


def _strong_card(name: str) -> bool:
    """百科大样本高胜率的卡：即使角色标签对不上公式也不应被硬过滤掉。"""
    row = ENCYCLOPEDIA.get(name)
    if not row:
        return False
    samples = (row['wins'] or 0) + (row['losses'] or 0)
    return samples >= 200 and (row['winrate'] or 0) >= 0.56


def _premium_dps(name: str, catalog: Mapping[str, Entry] = CATALOG) -> bool:
    """强力输出：用户钦定的输出核心, 或百科大样本胜率过半, 或特等且强度分够高。"""
    entry = catalog.get(name)
    if entry is None or 'damage' not in role_tags(entry):
        return False
    if name in USER_STRONG_DPS:
        return True
    row = ENCYCLOPEDIA.get(name) or {}
    samples = (row.get('wins') or 0) + (row.get('losses') or 0)
    if samples >= 150 and (row.get('winrate') or 0) >= 0.50:
        return True
    strength = STRENGTH_SCORES.get(name, {}).get('strength')
    return entry.tier == '特等' and isinstance(strength, (int, float)) and strength >= 35


def choose_pick(offers: Iterable[str], team: Iterable[str] = (), *,
                team_size: int = 5, speeds: Mapping[str, int] | None = None,
                defenses: Mapping[str, int] | None = None,
                catalog: Mapping[str, Entry] = CATALOG) -> Pick:
    """在当前候选中选一个；候选识别不完整时由界面层重试 OCR。"""
    current = tuple(team)
    if not 1 <= team_size <= 8 or len(current) >= team_size:
        raise ValueError('队伍人数配置无效或已满')
    resolved = [normalize_name(name, catalog) for name in offers]
    candidates = sorted({name for name in resolved if name and name not in current})
    if not candidates:
        raise ValueError('没有可识别且未入队的候选式神')
    existing_tags = [role_tags(catalog[name], defense=(defenses or {}).get(name))
                     for name in current]
    formula_hint = None

    def candidate_roles(name):
        return role_tags(catalog[name], defense=(defenses or {}).get(name))

    # 用户口径(2026-10-30): 初音未来只在队里有 sp千姬(鲸汐千姬) 时才拿, 单独拿没意义
    if '初音未来' in candidates and '鲸汐千姬' not in current and len(candidates) > 1:
        candidates = [name for name in candidates if name != '初音未来']
    # 椒图+莹草 已经把两个保护位占满, 剩下的人不能再选保护
    if {'椒图', '莹草'} <= set(current):
        trimmed = [name for name in candidates
                   if not ({'sustain', 'shield'} & candidate_roles(name))]
        if trimmed:
            candidates = trimmed

    # 第一手先拿最强卡(不套公式), 之后按编排优先序补位;
    # 高胜率强卡不受角色池硬过滤（如鬼王酒吞童子标为“综合”，不能被输出位筛掉）。
    def _shrunk(name):
        row = ENCYCLOPEDIA.get(name)
        if not row:
            return None
        samples = (row['wins'] or 0) + (row['losses'] or 0)
        if samples < 50:
            return None
        return ((row['wins'] or 0) + 50) / (samples + 100)

    # 硬过滤：百科胜率 < 45% 的坑位直接不选(除非整批候选都很差);
    # 用户钦定强卡和六番阵容里出现过的卡豁免（实战六番比小样本胜率更可信）
    viable = [name for name in candidates
              if name in USER_STRONG_CARDS or name in SIX_WIN_FREQUENCY
              or name in CORE_FIRE
              or (lambda v: v is None or v >= 0.45)(_shrunk(name))]
    if viable:
        candidates = viable

    strong_pool = [name for name in candidates
                   if name in USER_STRONG_CARDS or name in USER_STRONG_DPS
                   or (_strong_card(name)
                       and (lambda v: v is None or v >= 0.5)(_shrunk(name)))]

    def _widen(pool):
        return list(dict.fromkeys(pool + strong_pool))

    formula_allowed = len(current) >= 1
    counts = composition_counts(current, catalog, defenses)
    fire_short = max(0, FIRE_TARGET - counts['fire'])

    # 编排优先序（用户口径）：强输出 > 补到 2 打火 > 补到 2 输出 >
    # 1 治疗/盾 > 1 控制/拉条；强增伤(S)可以顶替后两个辅助位。
    def _dps_pool(floor: float) -> list[str]:
        return [name for name in candidates if 'damage' in candidate_roles(name)
                and (lambda v: v is None or v >= floor)(_shrunk(name))]

    if formula_allowed:
        fire_pool = [name for name in candidates if 'fire' in candidate_roles(name)]
        # 用户口径(2026-10-30): 整队至少要 2 个打火, 且其中至少 1 个真火;
        # 鬼金羊/青行灯/sp座敷这类假火只当第二火用, 不会替代真火
        core_fire = [name for name in fire_pool if name in CORE_FIRE]
        other_fire = [name for name in fire_pool if name not in CORE_FIRE]
        picks_left = team_size - len(current)
        # 用户口径(2026-10-30): 1 真火 + 1 假火就够用(追月神+青行灯/鬼金羊/sp座敷),
        # 但假火必须配真火, 所以先保底 1 个真火, 再补到 2 个总火
        core_have = sum(1 for item in current if item in CORE_FIRE)
        need_core = max(0, 1 - core_have) if core_fire else 0
        need_any = max(0, FIRE_TARGET - counts['fire'])
        any_pool = core_fire + other_fire
        steps = (
            (need_core > 0 and picks_left <= need_core,
             core_fire, '公式：一个真火都没有了，必须补真火', True),
            (need_any > 0 and picks_left <= need_any and any_pool,
             any_pool, f'公式：打火只有 {counts["fire"]} 个，必须补火', True),
            (counts['damage'] < 2,
             [name for name in candidates if _premium_dps(name, catalog)],
             '公式：强力输出优先拿', False),
            (need_core > 0 and picks_left <= need_core + 2,
             core_fire, '公式：缺真火，优先补真火', True),
            (need_any > 0 and picks_left <= need_any + 2 and any_pool,
             any_pool, f'公式：打火只有 {counts["fire"]} 个，优先补火', True),
            (counts['fire'] < 1 and picks_left <= 1 and fire_pool,
             fire_pool, '公式：最后一手还没火，实在没有真火就随便拿一个', True),
            (counts['damage'] < 2,
             _dps_pool(0.5) or _dps_pool(0.45),
             '公式：输出不足 2，优先补输出', False),
            (counts['protect'] < 1,
             [name for name in candidates
              if {'sustain', 'shield'} & candidate_roles(name)
              or ('support' in candidate_roles(name) and _is_amplifier(name, catalog)
                  and _strong_support(name))],
             '公式：缺少治疗/盾(强增伤可顶替)，优先补齐', False),
            (counts['flex'] < 1,
             [name for name in candidates
              if {'control', 'tempo'} & candidate_roles(name)
              or ('support' in candidate_roles(name) and _is_amplifier(name, catalog)
                  and _strong_support(name))],
             '公式：缺少控制/拉条(强增伤可顶替)，优先补齐', False),
        )
        for condition, pool, hint, strict in steps:
            if condition and pool:
                candidates = list(pool) if strict else _widen(pool)
                formula_hint = hint
                break
    baseline = team_score(current, catalog, speeds, defenses)
    existing_roles = set().union(*existing_tags)
    pick_table = battle_stats._tally('ours') if battle_stats is not None else None
    ranked = []
    for name in candidates:
        entry = catalog[name]
        gain = team_score((*current, name), catalog, speeds, defenses) - baseline
        # 前两手先抢强力输出(用户口径): 此时阵容还没成型, 公式分不该压过强输出
        if len(current) < 2 and counts['damage'] < 2 and _premium_dps(name, catalog):
            gain += 24
        # 火不够 2 个时, 真正的打火机再加一档(鬼金羊那种假火不加); 薰还要再高一档
        if fire_short > 0 and name in CORE_FIRE:
            gain += 8.0
            if name in PRIORITY_FIRE:
                gain += 5.0
        # 多套六番阵容里的常驻卡(思金神/神无月/卑弥呼/因幡...)优先度略高一档
        appearances = SIX_WIN_FREQUENCY.get(name, 0)
        if appearances >= 2:
            gain += 3.0 * min(3, appearances - 1)
            reasons_extra = f'六番阵容常驻：{appearances} 套'
        else:
            reasons_extra = None
        new_roles = role_tags(entry, defense=(defenses or {}).get(name)) - existing_roles
        source = STRENGTH_SCORES.get(name, {})
        reasons = [f'等级：{USER_TIERS.get(name, entry.tier)}']
        if isinstance(source.get('strength'), (int, float)):
            reasons.append(f"评分表：强度{source['strength']}，组合{source['combo']}")
        extra = ENCYCLOPEDIA.get(name)
        if extra:
            samples = (extra['wins'] or 0) + (extra['losses'] or 0)
            rating = extra['rating']
            if name in USER_RATINGS:
                rating = USER_RATINGS[name]
            if name == '茨木童子':
                rating = 'S' if '云间不见岳' in current else 'A'
            if name == '云外镜':
                rating = '白镜 A' if (defenses or {}).get(name, 0) >= 900 else '黑镜 D（形态未明按黑镜）'
            reasons.append(f'对弈百科：{rating}，{samples}场')
            if samples >= 150 and (extra['winrate'] or 0) < 0.36:
                # 弱势卡（如须佐之男/妖刀姬/以津真天）只有在没有更好候选时才拿
                gain -= 8
                reasons.append(f'弱势卡：胜率{extra["winrate"]:.0%}')
        if formula_hint:
            reasons.append(formula_hint)
        # 公式推荐名单加分：首选 +8，次选 +4（按候选式神实际角色的推荐顺序）。
        rec_bonus = 0
        rec_label = ''
        for role in sorted(role_tags(entry, defense=(defenses or {}).get(name))):
            recommend = ROLE_RECOMMEND.get(role, ())
            if name in recommend:
                rec_bonus += (RECOMMEND_BONUS if recommend.index(name) == 0
                              else RECOMMEND_BONUS_SECOND)
                rec_label = f'公式推荐：{role}首选 {recommend[0]}'
                break
        gain += rec_bonus
        if rec_label:
            reasons.append(rec_label)
        if reasons_extra:
            reasons.append(reasons_extra)
        # 实战统计: 本号上阵/初选的胜率加成, 样本不足时恒为 0
        stats_gain = 0.0
        if battle_stats is not None:
            stats_gain += battle_stats.pick_bonus(name, pick_table)
        if stats_gain:
            gain += stats_gain
            reasons.append(f'实战胜率加成 {stats_gain:+.1f}')
        if new_roles:
            labels = {'fire': '鬼火', 'sustain': '治疗', 'shield': '护盾',
                      'control': '控制', 'damage': '输出', 'tempo': '拉条',
                      'support': '辅助'}
            reasons.append('补位：' + '、'.join(labels[role] for role in sorted(new_roles)))
        ranked.append((gain, tier_points(entry, current, speed=(speeds or {}).get(name),
                                        defense=(defenses or {}).get(name)),
                       name, tuple(reasons)))
    gain, _, name, reasons = max(ranked, key=lambda item: (item[0], item[1], item[2]))
    return Pick(name, gain, reasons, suggested_souls(catalog[name]))


def plan_lineup(available: Iterable[str], *, team_size: int = 5,
                locked: Iterable[str] = (), speeds: Mapping[str, int] | None = None,
                defenses: Mapping[str, int] | None = None,
                max_fire: int | None = None,
                catalog: Mapping[str, Entry] = CATALOG,
                field_weights: Mapping[str, float] | None = None) -> tuple[str, ...]:
    """给定完整候选池时搜索高分阵容；locked 中的成员必须保留。"""
    required = tuple(locked)
    if not 1 <= team_size <= 8 or len(required) > team_size:
        raise ValueError('队伍人数配置无效')
    if len(required) != len(set(required)) or any(name not in catalog for name in required):
        raise ValueError('已选阵容含重复或未知式神')
    if len(SUMMON_SLOT_USERS.intersection(required)) > 1:
        raise ValueError('已选阵容占用多个我方召唤位')
    if max_fire is not None and (max_fire < 0 or
            sum(is_fire_supplier(name, catalog) for name in required) > max_fire):
        raise ValueError('已上阵打火式神数量超过限制')
    candidates = sorted({name for raw in available
                         if (name := normalize_name(raw, catalog)) and name not in required})
    if len(candidates) + len(required) < team_size:
        raise ValueError('可识别候选不足以组成队伍')
    # 保留多种中途组合，让火机、盾和输出的搭配可以在后几手形成。
    field_table = battle_stats._tally('fielded') if battle_stats is not None and field_weights is None else None
    states = [required]
    for _ in range(team_size - len(required)):
        next_states = {}
        for state in states:
            for name in candidates:
                if name not in state:
                    lineup = (*state, name)
                    if len(SUMMON_SLOT_USERS.intersection(lineup)) > 1:
                        continue
                    if max_fire is not None and sum(
                            is_fire_supplier(item, catalog) for item in lineup) > max_fire:
                        continue
                    key = tuple(sorted(lineup))
                    next_states[key] = lineup
        if not next_states:
            raise ValueError('召唤位或打火数量限制后候选不足以组成阵容')
        states = sorted(next_states.values(),
                        key=lambda lineup: (team_score(lineup, catalog, speeds, defenses) +
                                            (sum(field_weights.get(item, 0.0) for item in lineup) if field_weights is not None
                                             else sum(battle_stats.field_bonus(item, field_table)
                                                      for item in lineup) if battle_stats else 0.0),
                                            lineup),
                        reverse=True)[:128]
    return states[0]


def plan_duel_lineup(own: Iterable[str], mate: Iterable[str], *, locked: Iterable[str] = (),
                     mate_locked: Iterable[str] = (),
                     team_size: int = 3,
                     field_weights: Mapping[str, float] | None = None,
                     catalog: Mapping[str, Entry] = CATALOG) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """协同对弈联合规划：两个号共 6 个位置按公式分配。

    公式（用户口径）：2 打火 + 2 输出 + 1 治疗/盾 + 1 控制或拉条, 强力增伤可顶替后两个辅助位;
    两边合计 6 人；火机可全在一侧（如 2+0），不必每边 1 个。
    评分使用 team_score（不带速度/防御）及双方上阵式神的实战胜率加分。
    返回 (own_pick, mate_pick)，各 team_size 个。
    """
    from itertools import combinations
    own_pool = sorted({name for raw in own if (name := normalize_name(raw, catalog))})
    mate_pool = sorted({name for raw in mate if (name := normalize_name(raw, catalog))})
    required = tuple(locked)
    mate_required = tuple(mate_locked)
    if len(own_pool) < team_size or len(mate_pool) < team_size:
        raise ValueError('协同对弈任一账号备选不足')
    formulas = (
        # 档1 严格：2 打火 + 至少 2 输出 + 1 治疗/盾 + 1 控制或拉条(强增伤算万能位)
        lambda c: (c['fire'] >= 2 and c['damage'] >= 2 and
                   c['protect'] >= 1 and c['flex'] >= 1),
        # 档2：至少 1 火 + 至少 2 输出 + 1 保护
        lambda c: c['fire'] >= 1 and c['damage'] >= 2 and c['protect'] >= 1,
        # 档3：至少有火有输出（强组合常会多带输出/增伤, 这里不卡死数量）
        lambda c: c['fire'] >= 1 and c['damage'] >= 1,
        # 档4 兜底
        lambda c: True,
    )
    field_table = battle_stats._tally('fielded') if battle_stats is not None and field_weights is None else None
    for formula in formulas:
        best, best_rank = None, (-1e18, -1)
        for own_pick in combinations(own_pool, team_size):
            if not set(required).issubset(own_pick):
                continue
            for mate_pick in combinations(mate_pool, team_size):
                if not set(mate_required).issubset(mate_pick):
                    continue
                counts = composition_counts((*own_pick, *mate_pick), catalog)
                if not formula(counts):
                    continue
                try:
                    score = team_score((*own_pick, *mate_pick), catalog)
                except Exception:
                    continue
                if field_weights is not None:
                    score += sum(field_weights.get(item, 0.0) for item in (*own_pick, *mate_pick))
                elif battle_stats is not None:
                    score += sum(battle_stats.field_bonus(item, field_table)
                                 for item in (*own_pick, *mate_pick))
                # 用户口径(2026-10-29): 奶已经够了就别再塞输出, 该补控制
                # (例: 有 灼华桃花妖 当奶, 多出来的输出位应换成 百目鬼 这种控制)。
                # 所以结构优先于纯分: 有控制/有保护 > 输出数不超过 2 > 综合分。
                # 用户口径(2026-10-29 修正): 不是"缺奶/缺控制扣分", 而是
                # "保护位(盾/奶)只要 1 个, 两个保护要扣分"; 另一个辅助位最好是
                # 控制 / 增伤 / 行动条(拉条)。所以: 保护>1 重罚, 1保护+1功能位加分。
                if counts['protect'] > 1:
                    score -= 15.0 * (counts['protect'] - 1)
                if counts['protect'] >= 1 and counts['flex'] >= 1:
                    score += 6.0
                if counts['damage'] > 2:
                    score -= 12.0 * (counts['damage'] - 2)
                if counts.get('heal_only', 0) >= 2:
                    score -= 10.0 * (counts['heal_only'] - 1)
                rank = (score, -abs(counts['damage'] - 2))
                if rank > best_rank:
                    best_rank, best = rank, (own_pick, mate_pick)
        if best is not None:
            return best
    raise ValueError('协同对弈联合规划无解')
