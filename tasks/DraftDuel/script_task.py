"""协同对弈任务入口：只编排大轮和小局各阶段。"""

from copy import deepcopy
from module.exception import TaskEnd
from module.draft_duel_schedule import require_draft_duel_hours
from tasks.Component.GeneralBattle.assets import GeneralBattleAssets
from tasks.Component.GeneralInvite.general_invite import GeneralInvite
from tasks.DraftDuel.assets import DraftDuelAssets
from tasks.GameUi.game_ui import GameUi
from tasks.DraftDuel.runtime.round_state import DraftDuelRoundStateMixin
from tasks.DraftDuel.runtime.invitation import DraftDuelInvitationMixin
from tasks.DraftDuel.runtime.selection import DraftDuelSelectionMixin
from tasks.DraftDuel.runtime.matchmaking import DraftDuelMatchmakingMixin
from tasks.DraftDuel.runtime.deployment import DraftDuelDeploymentMixin
from tasks.DraftDuel.runtime.battle import DraftDuelBattleMixin
from tasks.DraftDuel.runtime.cooperation import DraftDuelCooperationMixin
from tasks.DraftDuel.runtime.cleanup import DraftDuelCleanupMixin
from tasks.DraftDuel.runtime.statistics import DraftDuelStatisticsMixin
from tasks.DraftDuel.runtime.recovery import DraftDuelRecoveryMixin


class ScriptTask(
    DraftDuelRoundStateMixin,
    DraftDuelInvitationMixin,
    DraftDuelSelectionMixin,
    DraftDuelMatchmakingMixin,
    DraftDuelDeploymentMixin,
    DraftDuelBattleMixin,
    DraftDuelCooperationMixin,
    DraftDuelCleanupMixin,
    DraftDuelStatisticsMixin,
    DraftDuelRecoveryMixin,
    GeneralInvite,
    GameUi,
    DraftDuelAssets,
):
    """大厅、选人、匹配、上阵、战斗与结算的阶段编排。"""

    MEMBER_START_TIMEOUT = 600
    SMALL_GAME_BATTLE_WINDOW = 300

    def __init__(self, config, device):
        # 皮肤替换只修改本任务实例，避免两个客户端的不同皮肤互相覆盖。
        self.I_BATTLE_INFO = deepcopy(GeneralBattleAssets.I_BATTLE_INFO)
        super().__init__(config, device)

    def _battle_info_rule(self):
        return getattr(self, 'I_BATTLE_INFO', GeneralBattleAssets.I_BATTLE_INFO)

    def run(self):
        """按大轮执行选人，再循环小局的匹配、上阵、战斗和结算。"""
        require_draft_duel_hours(self.config)
        self.draft_completed_rounds = 0
        self.draft_big_round = 0
        self._draft_wait_lobby_seen = False
        self._draft_resume_phase = 'lobby'
        self._initialize_duel_state()
        # 每次启动从大厅流程开始，不自动恢复上次运行保存的对局。
        phase = 'lobby'
        while True:
            require_draft_duel_hours(self.config)
            if phase == 'battle' and getattr(self, '_draft_deployment_state', None):
                state = self._draft_deployment_state
                self._finish_deployment_on_battle(state['pending'], state['slots'])
            self._save_draft_checkpoint(phase)
            if phase in ('rank', 'fight') and len(self.draft_selected) != 5:
                raise TaskEnd('协同对弈恢复缺少本轮五名式神记录，不能猜测名字与头像对应关系')
            if phase in ('battle', 'settlement'):
                following = self._battle_and_settlement()
                if following in ('rank', 'lobby'):
                    phase = following
                    continue
                self.set_next_run(task='DraftDuel', success=False, finish=False)
                raise TaskEnd('协同对弈战斗或结算阶段未完成')
            started = self._enter_matchmaking() if phase == 'lobby' else True
            if started and phase == 'lobby':
                self._save_draft_checkpoint('select')
            drafted = self._select_shikigami() if started and phase in ('lobby', 'select') else started
            if drafted and phase != 'fight':
                self._save_draft_checkpoint('rank')
            matched = self._match_battle() if drafted and phase != 'fight' else drafted
            if matched == 'lobby':
                phase = 'lobby'
                continue
            if matched:
                self._save_draft_checkpoint('fight')
            deployed = self._deploy_shikigami() if matched else False
            if deployed:
                self._save_draft_checkpoint('battle')
            following = self._battle_and_settlement() if deployed else None
            if following in ('rank', 'lobby'):
                phase = following
                continue
            self.set_next_run(task='DraftDuel', success=False, finish=False)
            if deployed:
                raise TaskEnd('协同对弈战斗或结算阶段未完成')
            if matched:
                raise TaskEnd('协同对弈上阵阶段未完成')
            if drafted:
                raise TaskEnd('协同对弈匹配阶段未完成')
            if started:
                raise TaskEnd('协同对弈选人阶段等待超时')
            raise TaskEnd('协同对弈未发起匹配')
