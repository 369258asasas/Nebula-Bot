"""
==================================================
                星云框架-法律声明与使用条款
==================================================

重要提示：在使用本框架前，请仔细阅读以下法律声明和使用条款。
继续使用本框架即表示您已完全理解并同意遵守以下所有条款。

【禁止事项】
1. 严禁使用本框架从事任何非法活动，包括但不限于：
   - 网络攻击、入侵、破坏他人系统
   - 传播病毒、木马、恶意软件
   - 进行网络诈骗、钓鱼等违法犯罪行为
   - 侵犯他人隐私、窃取他人信息
   - 传播违法、违规内容

2. 转发与分发限制：
   - 严禁未经授权转发、分发本框架
   - 严禁对本框架进行逆向工程、反编译
   - 严禁擅自修改框架核心代码

【责任声明】
1. 使用者需自行承担使用本框架带来的所有风险和责任
2. 因违规使用本框架导致的任何损失，包括但不限于：
   - 服务器损害、数据丢失
   - 法律纠纷、行政处罚
   - 经济损失、声誉损害
   均由使用者自行承担全部责任

3. 框架开发者不对以下情况承担责任：
   - 使用者违规操作造成的任何后果
   - 第三方插件造成的安全风险
   - 系统兼容性问题导致的损失
   - 因框架使用不当造成的业务中断

【知识产权】
1. 本框架及其相关文档受版权法保护
2. 未经明确授权，不得用于商业用途
3. 保留所有权利

【遵守法律】
使用者应确保其使用行为符合所在国家/地区的法律法规，
包括但不限于网络安全法、数据保护法等相关法律。

如果您使用的是ai编写插件，请在合法合理的范围内进行编写，切勿进行违法用途!

只支持android(容器)/Linux系统，不支持其他系统！！！

==================================================
如果您不同意以上任何条款，请立即停止使用本框架。
==================================================
"""

import os
import re
from datetime import datetime

class Config:
    FRAMEWORK_VERSION = "8.0.0"  #版本号
    
    TOKEN = "369258asasASAS?!"  #token密钥
    
    API_BASE_URL = "http://localhost:3000"
    
    EVENT_SERVER_HOST = "127.0.0.1"
    EVENT_SERVER_PORT = 8080   #端口/ip这些尽量别动
    
    PLUGINS_DIR = "plugins"
    
    LOG_LEVEL = "INFO"
    LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    LOG_FILE_MAX_DAYS = 1   #日志保留时间
    
    PLUGIN_EVENT_TIMEOUT = 120    #插件超时时间
    
    
    BOT_QQ = 3911335816   #机器人qq号
    ADMIN_QQ = 3342514896   #主人qq号
    ENABLE_DEBUG = False   #调试模式，非开发者人员勿开(后果自负，如果开启)
    
    HOT_RELOAD = True   #热重载开关
    HOT_RELOAD_INTERVAL = 5   #热重载检查时间(正常情况下不需要动)
    
    DAEMON_MODE = True   #守护进程开关(一般是给服务器用的)
    
    AUTO_INSTALL_MODULES = True   #模块补全开关
    MODULE_INSTALL_TIMEOUT = 120  #超时时间(正常情况下不需要动)
    
    STARTUP_REJECT_EVENTS = False   #启动期拒绝接收事件开关
    STARTUP_REJECT_DURATION = 20   #拒绝时间(默认即可)
    
    ENABLE_REQUEST_DEDUPLICATION = False   #api信息去重开关
    ENABLE_EVENT_DEDUPLICATION = False   #事件去重开关
    REQUEST_CLEANUP_INTERVAL = 30
    REQUEST_EXPIRE_TIME = 360
    REQUEST_WAIT_TIMEOUT = 5
    EVENT_DEDUPLICATION_WINDOW = 5  # 事件去重时间窗口（秒）

    # API请求超时配置
    API_REQUEST_TIMEOUT_NORMAL = 10  # 普通API请求超时时间（秒）
    API_REQUEST_TIMEOUT_LONG = 60    # 长操作API请求超时时间（秒）
    API_REQUEST_MAX_RETRIES = 3      # API请求最大重试次数

#   =============以下为系统核心配置非技术人员请勿修改(否则后果自负)============#
    # 日志开关不动就行，保持默认即可别瞎搞
    ENABLE_STARTUP_LOG_CLEANUP = True  #过期日志删除开关
    ENABLE_SHUTDOWN_LOG_CLEANUP = True  #框架关闭日志删除开关
    ENABLE_RUNTIME_LOG_CLEANUP = True  #运行日志清理开关
    
    # 插件超时取消配置(别瞎搞)
    PLUGIN_CANCEL_WAIT_TIMEOUT = 1.0  # 插件取消等待时间（秒）- 改为1秒
    PLUGIN_FORCE_RELOAD_TIMEOUT = 0.5  # 插件强制重载超时时间（秒）

    @staticmethod
    def get_log_filename():
        return "logs/bot_server.log"
    
    @staticmethod
    def get_api_log_filename():
        return "logs/bot_api.log"
                
    @staticmethod
    def get_current_version():
        return Config.FRAMEWORK_VERSION
    
    @staticmethod
    def validate_startup_duration():
        if Config.STARTUP_REJECT_DURATION < 10:
            Config.STARTUP_REJECT_DURATION = 10
            return 10
        return Config.STARTUP_REJECT_DURATION
    
    @staticmethod
    def validate_request_deduplication_config():
        if Config.REQUEST_CLEANUP_INTERVAL < 60:
            Config.REQUEST_CLEANUP_INTERVAL = 60
        if Config.REQUEST_EXPIRE_TIME < 300:
            Config.REQUEST_EXPIRE_TIME = 300
        if Config.REQUEST_WAIT_TIMEOUT < 10:
            Config.REQUEST_WAIT_TIMEOUT = 10
        return True