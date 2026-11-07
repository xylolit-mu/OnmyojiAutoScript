from importlib import import_module

from module.config.config_model import ConfigModel
from module.config.utils import convert_to_underscore
from module.exception import TaskEnd
from module.logger import logger
from tasks.Component.SwitchAccount.switch_account import SwitchAccount
from tasks.CustomTask.config import TASK_TYPES
from tasks.GameUi.game_ui import GameUi
from tasks.MetaDemon.config import MetaDemonMode


class EntryModel(ConfigModel):
    def save(self):
        return None


class EntryConfig:
    """Provide a private task configuration and capture its scheduling outcome."""

    cooperation_runtime = None
    defer_task_courtyard_return = True

    def __init__(self, config, entry, settings):
        self._config = config
        self.task = convert_to_underscore(entry.type)
        snapshot = config.model.model_copy(deep=True)
        fields = vars(snapshot).copy()
        if entry.type != 'AccountSwitch':
            fields[self.task] = settings
        self.model = EntryModel.model_construct(**fields)
        self._cooperation_peers = {}
        self.success = None

    def __getattr__(self, name):
        if name in type(self.model).model_fields:
            return getattr(self.model, name)
        return getattr(self._config, name)

    def task_delay(self, task, *, success=None, **kwargs):
        if convert_to_underscore(task) == self.task:
            self.success = success
        else:
            logger.info(f'自定义任务忽略子任务的额外调度：{task}')

    def task_call(self, task=None, **kwargs):
        logger.info(f'自定义任务忽略子任务的调度请求：{task}')

    def save(self):
        # Changes made by existing task implementations are local to this run.
        return None


class ScriptTask(GameUi):
    @staticmethod
    def entry_error(entry):
        settings = entry.task_model()
        if entry.type == 'AccountSwitch':
            account = settings.account
            if not all(value.strip() for value in (account.account, account.svr)):
                return '账号切换需要填写目标账号和服务器，登录角色名可留空'
        invite = getattr(settings, 'invite_config', None)
        if ((invite and getattr(invite, 'cooperation_script', ''))
                or (entry.type == 'MetaDemon' and MetaDemonMode.BOSS_ASSIST in settings.meta_demon_config.mode)):
            return '编组条目暂不支持跨脚本协同，请通过独立任务执行'
        return None

    def run(self):
        entries = [entry.model_copy(deep=True) for entry in self.config.model.custom_task.entries]
        success = bool(entries)
        if not entries:
            logger.info('自定义任务列表为空')
        for index, entry in enumerate(entries, 1):
            error = self.entry_error(entry)
            if error:
                logger.warning(f'自定义任务第 {index} 项：{error}')
                self.set_next_run('CustomTask', success=False, finish=True)
                raise TaskEnd
        for index, entry in enumerate(entries, 1):
            label = TASK_TYPES[entry.type][0]
            logger.hr(f'自定义任务 {index}/{len(entries)}：{label}', 2)
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            if not self.run_entry(entry):
                logger.warning(f'自定义任务第 {index} 项（{label}）未完成，停止本轮')
                success = False
                break
        self.set_next_run('CustomTask', success=success, finish=True)
        self.finish_task_navigation()
        raise TaskEnd

    def run_entry(self, entry):
        error = self.entry_error(entry)
        if error:
            logger.warning(error)
            return False
        settings = entry.task_model()
        config = EntryConfig(self.config, entry, settings)
        if entry.type == 'AccountSwitch':
            account = settings.account
            return SwitchAccount(config, self.device, account).switchAccount()
        runner = import_module(f'tasks.{entry.type}.script_task').ScriptTask(config, self.device)
        runner._battle_cooperation_prepared = True
        try:
            result = runner.run()
        except TaskEnd:
            return config.success is True
        return config.success is True if config.success is not None else result is not False
