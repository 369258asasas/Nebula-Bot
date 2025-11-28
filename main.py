import json
from datetime import datetime
import logging
from config import Config
from api import bot_api
import importlib
import importlib.util
import os
import sys
import gc
import traceback
import time
import asyncio
import aiohttp
from aiohttp import web
import re
import hashlib
import inspect
import glob
from logging.handlers import TimedRotatingFileHandler
from server_manager import ServerManager, PluginContext
import subprocess
import signal
from shared_state import global_state, readonly_global_state, PluginStateAccessor
from app import BotApplication

def check_system_platform():
    platform = sys.platform.lower()
    
    if platform == "linux":
        if hasattr(os, 'uname'):
            uname_result = os.uname()
            if 'android' in uname_result.sysname.lower() or 'android' in uname_result.version.lower():
                return "android"
        return "linux"
    
    unsupported_systems = {
        "win32": "Windows",
        "cygwin": "Windows/Cygwin", 
        "darwin": "macOS",
        "os2": "OS/2",
        "os2emx": "OS/2 EMX"
    }
    
    if platform in unsupported_systems:
        return f"unsupported_{unsupported_systems[platform]}"
    
    return "unknown"

system_platform = check_system_platform()

if system_platform.startswith("unsupported"):
    print(f"错误：此框架仅支持Linux和Android系统，检测到当前系统: {system_platform.replace('unsupported_', '')}")
    print("请在Linux或Android环境中运行此框架")
    sys.exit(1)

if system_platform == "unknown":
    print("警告：无法识别当前操作系统平台，框架可能无法正常运行")
    print("建议在Linux或Android环境中运行此框架")

def validate_token_strength(token):
    if not token:
        return False, "Token未设置，请设置一个强密码"

    if len(token) < 16:
        return False, "Token长度不足，至少需要16个字符"

    has_upper = bool(re.search(r'[A-Z]', token))
    has_lower = bool(re.search(r'[a-z]', token))
    has_digit = bool(re.search(r'[0-9]', token))
    has_special = bool(re.search(r'[^A-Za-z0-9]', token))

    complexity_score = sum([has_upper, has_lower, has_digit, has_special])
    if complexity_score < 3:
        return False, "Token复杂度不足，需要包含大小写字母、数字和特殊字符"

    return True, "Token强度足够"

token_valid, token_msg = validate_token_strength(Config.TOKEN)
if not token_valid:
    print(f"Token验证失败: {token_msg}")
    print("框架启动中止，请修改config.py中的TOKEN配置")
    sys.exit(1)

Config.validate_startup_duration()

base_dir = os.path.dirname(os.path.abspath(__file__))
logs_dir = os.path.join(base_dir, 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

plugins_dir = os.path.join(base_dir, 'plugins')
if not os.path.exists(plugins_dir):
    os.makedirs(plugins_dir)
    
LOG_FILENAME = os.path.join(logs_dir, "bot_server.log")
API_LOG_FILENAME = os.path.join(logs_dir, "bot_api.log")

def setup_logging():
    logger = logging.getLogger("BotServer")
    logger.setLevel(Config.LOG_LEVEL)

    api_logger = logging.getLogger("BotAPI")
    api_logger.setLevel(logging.INFO)

    logger.handlers.clear()
    api_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(Config.LOG_LEVEL)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)

    main_file_handler = logging.FileHandler(LOG_FILENAME, 'a', encoding='utf-8')
    main_file_handler.setLevel(logging.DEBUG)
    main_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s')
    main_file_handler.setFormatter(main_formatter)

    api_file_handler = logging.FileHandler(API_LOG_FILENAME, 'a', encoding='utf-8')
    api_file_handler.setLevel(logging.INFO)
    api_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    api_file_handler.setFormatter(api_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(main_file_handler)
    api_logger.addHandler(api_file_handler)

    if Config.ENABLE_DEBUG:
        logger.setLevel(logging.DEBUG)
        console_handler.setLevel(logging.DEBUG)
        logging.getLogger('aiohttp').setLevel(logging.INFO)
        logging.getLogger('asyncio').setLevel(logging.INFO)
        logger.debug("调试模式已启用，将记录详细日志信息")
    else:
        logging.getLogger('aiohttp').setLevel(logging.WARNING)
        logging.getLogger('asyncio').setLevel(logging.WARNING)

    return logger, api_logger

def clean_old_logs_on_startup():
    """启动时清理旧日志文件 - 只删除插件日志文件，不删除系统日志文件"""
    if not Config.ENABLE_STARTUP_LOG_CLEANUP:
        return
        
    logger = logging.getLogger("BotServer")
    logger.info("启动时清理插件日志文件...")
    
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(base_dir, 'logs')
        if not os.path.exists(log_dir):
            logger.info("日志目录不存在，无需清理")
            return
            
        deleted_count = 0
        for filename in os.listdir(log_dir):
            filepath = os.path.join(log_dir, filename)
            if (os.path.isfile(filepath) and 
                filename.startswith('plugin_') and 
                filename.endswith('.log')):
                try:
                    os.remove(filepath)
                    deleted_count += 1
                    logger.debug(f"已删除插件日志文件: {filename}")
                except Exception as e:
                    logger.warning(f"无法删除插件日志文件 {filename}: {str(e)}")
        
        if deleted_count > 0:
            logger.info(f"启动清理完成，删除了 {deleted_count} 个插件日志文件")
        else:
            logger.info("没有需要清理的插件日志文件")
            
    except Exception as e:
        logger.error(f"启动清理插件日志文件时出错: {str(e)}")

def clean_old_logs_on_shutdown():
    """关闭时清理旧日志文件 - 根据时间删除插件日志文件，不删除系统日志文件"""
    if not Config.ENABLE_SHUTDOWN_LOG_CLEANUP:
        return
        
    logger = logging.getLogger("BotServer")
    logger.info("关闭时清理插件日志文件...")
    
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(base_dir, 'logs')
        if not os.path.exists(log_dir):
            return
            
        current_time = time.time()
        cutoff_time = current_time - (Config.LOG_FILE_MAX_DAYS * 86400)
        
        deleted_count = 0
        for filename in os.listdir(log_dir):
            filepath = os.path.join(log_dir, filename)
            if (os.path.isfile(filepath) and 
                filename.startswith('plugin_') and 
                filename.endswith('.log')):
                try:
                    file_mtime = os.path.getmtime(filepath)
                    if file_mtime < cutoff_time:
                        os.remove(filepath)
                        deleted_count += 1
                except Exception:
                    pass
        
        if deleted_count > 0:
            logger.info(f"关闭清理完成，删除了 {deleted_count} 个旧插件日志文件")
            
    except Exception as e:
        logger.error(f"关闭清理插件日志文件时出错: {str(e)}")

def daemonize():
    """守护进程化函数"""
    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)
    except OSError as e:
        print(f"第一次fork失败: {e}", file=sys.stderr)
        os._exit(1)
    
    os.setsid()
    os.umask(0)
    
    try:
        pid = os.fork()
        if pid > 0:
            os._exit(0)
    except OSError as e:
        print(f"第二次fork失败: {e}", file=sys.stderr)
        os._exit(1)
    
    sys.stdout.flush()
    sys.stderr.flush()
    
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())
    
    pid_file = os.path.join(os.path.dirname(__file__), "bot_server.pid")
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

async def main():
    try:
        logger, api_logger = setup_logging()
        
        platform_info = check_system_platform()
        if platform_info == "android":
            logger.info("检测到运行环境: Android")
        elif platform_info == "linux":
            logger.info("检测到运行环境: Linux")
        else:
            logger.warning(f"未知运行环境: {platform_info}")
        
        clean_old_logs_on_startup()
        
        app = BotApplication(logger, api_logger)
        
        await app.initialize()
        
        await app.run_server()
        
    except Exception as e:
        logger.critical(f"应用程序启动失败: {str(e)}", exc_info=True)
        raise
    finally:
        clean_old_logs_on_shutdown()

if __name__ == "__main__":
    logger, api_logger = setup_logging()
    
    if Config.DAEMON_MODE:
        daemonize()
        logger.info("以守护进程模式启动")
    
    asyncio.run(main())
