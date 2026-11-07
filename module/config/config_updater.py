# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey
import re
from copy import deepcopy
from pathlib import Path

from cached_property import cached_property

from deploy.utils import DEPLOY_TEMPLATE, poor_yaml_read, poor_yaml_write
from module.base.timer import timer
from module.config.utils import *

class ConfigUpdater:

    @cached_property
    def args(self):
        return read_file(filepath_args(filename='args'))

    @timer
    def update_template(self, template_name: str = "template") -> None:
        """
        更新模板 。从args.json更新
        :param template_name:
        :return:
        """
        pass

    @timer
    def update_config(self, config_name: str) -> None:
        """启动时应用现有配置迁移和校验，仅在内容变化时写回。"""
        from module.config.config_model import ConfigModel

        path = Path(filepath_config(config_name))
        if not path.is_file():
            return
        with FileLock(f'{path}.lock'):
            raw = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict):
                raise ValueError(f'配置文件必须包含 JSON 对象：{path}')
            # 传入配置数据，避免模型在持锁期间再次从磁盘读取。
            values = {'running_task': '', **raw, 'config_name': config_name}
            model = ConfigModel(**values)
            serialized = json.dumps(model.model_dump(), indent=2, ensure_ascii=False, default=str)
            if json.loads(serialized) == raw:
                return
            with atomic_write(path, overwrite=True, encoding='utf-8', newline='') as stream:
                stream.write(serialized)
            logger.info(f'配置已更新为当前版本：{path}')

    def read_file(self, config_name, is_template=False):
        """
        Read and update config file.

        Args:
            config_name (str): ./config/{file}.json
            is_template (bool):

        Returns:
            dict:
        """
        old = read_file(filepath_config(config_name))
        # new = self.config_update(old, is_template=is_template)
        # The updated config did not write into file, although it doesn't matters.
        # Commented for performance issue
        # self.write_file(config_name, new)
        return old

    @staticmethod
    def write_file(config_name, data, mod_name='alas'):
        """
        Write config file.

        Args:
            config_name (str): ./config/{file}.json
            data (dict):
            mod_name (str):
        """
        write_file(filepath_config(config_name, mod_name), data)

