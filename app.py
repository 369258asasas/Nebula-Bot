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
from logging.handlers import TimedRotatingFileHandler
from server_manager import ServerManager, PluginContext
import subprocess
import signal
from shared_state import global_state, readonly_global_state, PluginStateAccessor

class StartupEventRejector:
    def __init__(self):
        self.startup_time = time.time()
        self.reject_duration = Config.validate_startup_duration()
        self.reject_end_time = self.startup_time + self.reject_duration
        self.rejected_count = 0
        
    def is_startup_period(self):
        return time.time() < self.reject_end_time
    
    def get_remaining_time(self):
        remaining = self.reject_end_time - time.time()
        return max(0, remaining)
    
    def should_reject_event(self):
        if not Config.STARTUP_REJECT_EVENTS:
            return False
        
        if self.is_startup_period():
            self.rejected_count += 1
            return True
        return False
    
    def get_status(self):
        return {
            "enabled": Config.STARTUP_REJECT_EVENTS,
            "startup_time": self.startup_time,
            "reject_end_time": self.reject_end_time,
            "reject_duration": self.reject_duration,
            "rejected_count": self.rejected_count,
            "is_active": self.is_startup_period(),
            "remaining_time": self.get_remaining_time()
        }
    
    def get_status_display(self):
        status = self.get_status()
        enabled_str = "启用" if status["enabled"] else "禁用"
        active_str = "活跃" if status["is_active"] else "结束"
        remaining = status["remaining_time"]
        
        return (f"启动拒绝期: {enabled_str} | 状态: {active_str} | "
                f"剩余时间: {remaining:.1f}秒 | 已拒绝事件: {status['rejected_count']}")

class LogCleaner:
    def __init__(self, logger):
        self.logger = logger
        self.last_runtime_cleanup = 0
        
    def clean_runtime_logs(self):
        """运行时日志清理 - 清空文件内容而不是删除文件"""
        if not Config.ENABLE_RUNTIME_LOG_CLEANUP:
            return
            
        current_time = time.time()
        if current_time - self.last_runtime_cleanup < 86400:  
            return
            
        self.last_runtime_cleanup = current_time
        self.logger.info("开始运行时日志清理...")
        
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(base_dir, 'logs')
            if not os.path.exists(log_dir):
                return
                
            cleaned_count = 0
            for filename in os.listdir(log_dir):
                if filename.endswith('.log'):
                    filepath = os.path.join(log_dir, filename)
                    if os.path.isfile(filepath):
                        try:
                            file_mtime = os.path.getmtime(filepath)
                            file_age = current_time - file_mtime
                            
                            if file_age > Config.LOG_FILE_MAX_DAYS * 86400:
                                with open(filepath, 'w', encoding='utf-8') as f:
                                    f.write('')
                                cleaned_count += 1
                                self.logger.debug(f"已清空日志文件: {filename}")
                        except Exception as e:
                            self.logger.warning(f"无法清空日志文件 {filename}: {str(e)}")
            
            if cleaned_count > 0:
                self.logger.info(f"运行时日志清理完成，清空了 {cleaned_count} 个过期日志文件")
            else:
                self.logger.debug("没有需要清理的过期日志文件")
                
        except Exception as e:
            self.logger.error(f"运行时日志清理出错: {str(e)}")

    def clean_plugin_log_file(self, plugin_name):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(base_dir, 'logs')
            log_file = os.path.join(log_dir, f"plugin_{plugin_name}.log")
            
            if os.path.exists(log_file):
                os.remove(log_file)
                self.logger.debug(f"已清理插件日志文件: {plugin_name}")
        except Exception as e:
            self.logger.warning(f"清理插件日志文件失败 {plugin_name}: {str(e)}")

class DeduplicationManager:
    """去重管理器，统一管理API和事件去重"""
    def __init__(self):
        self.api_request_tracker = {}
        self.event_tracker = {}
        self.last_cleanup_time = time.time()
        
    def _generate_request_id(self, endpoint, params):
        """生成API请求ID"""
        params_str = json.dumps(params, sort_keys=True) if params else "{}"
        request_str = f"{endpoint}_{params_str}"
        return hashlib.md5(request_str.encode('utf-8')).hexdigest()
    
    def _generate_event_id(self, event_data):
        """生成事件ID"""
        event_str = json.dumps(event_data, sort_keys=True) if event_data else "{}"
        return hashlib.md5(event_str.encode('utf-8')).hexdigest()
    
    def _cleanup_old_entries(self):
        """清理过期条目"""
        current_time = time.time()
        if current_time - self.last_cleanup_time < Config.REQUEST_CLEANUP_INTERVAL:
            return
        
        self.last_cleanup_time = current_time
        
        expired_requests = []
        for request_id, request_data in self.api_request_tracker.items():
            if current_time - request_data.get("timestamp", 0) > Config.REQUEST_EXPIRE_TIME:
                expired_requests.append(request_id)
        
        for request_id in expired_requests:
            del self.api_request_tracker[request_id]
        
        expired_events = []
        for event_id, event_data in self.event_tracker.items():
            if current_time - event_data.get("timestamp", 0) > Config.EVENT_DEDUPLICATION_WINDOW:
                expired_events.append(event_id)
        
        for event_id in expired_events:
            del self.event_tracker[event_id]
    
    def check_api_request(self, endpoint, params):
        """检查API请求是否重复"""
        if not Config.ENABLE_REQUEST_DEDUPLICATION:
            return None
        
        self._cleanup_old_entries()
        
        request_id = self._generate_request_id(endpoint, params)
        
        if request_id in self.api_request_tracker:
            request_data = self.api_request_tracker[request_id]
            
            if request_data.get("status") == "processing":
                return "processing"
            elif request_data.get("status") == "completed":
                return request_data.get("result")
        
        self.api_request_tracker[request_id] = {
            "status": "processing",
            "timestamp": time.time(),
            "endpoint": endpoint,
            "params": params
        }
        
        return None
    
    def complete_api_request(self, request_id, result):
        """完成API请求"""
        if request_id in self.api_request_tracker:
            if result.get("status") == "ok" and result.get("retcode") == 0:
                self.api_request_tracker[request_id] = {
                    "status": "completed",
                    "timestamp": time.time(),
                    "result": result
                }
            else:
                del self.api_request_tracker[request_id]
    
    def check_event(self, event_data):
        """检查事件是否重复"""
        if not Config.ENABLE_EVENT_DEDUPLICATION:
            return False
        
        self._cleanup_old_entries()
        
        event_id = self._generate_event_id(event_data)
        
        if event_id in self.event_tracker:
            return True
        
        self.event_tracker[event_id] = {
            "timestamp": time.time(),
            "event_data": event_data
        }
        
        return False

class PluginManager:
    def __init__(self, server_manager):
        self.plugins = []
        self.plugin_files = {}
        self.plugin_modules = {}
        self.plugins_dir = os.path.join(os.path.dirname(__file__), Config.PLUGINS_DIR)
        self.installed_modules = set()
        self.error_history = {}
        self._lock = asyncio.Lock()
        self.last_full_check = 0
        self.startup_rejector = StartupEventRejector()
        self.log_cleaner = LogCleaner(server_manager.logger)
        self.plugin_contexts = {}
        self._context_lock = asyncio.Lock()
        self.initial_loading_complete = False
        self._server_manager = server_manager
        self._plugin_path_inserted = False
        self.deduplication_manager = DeduplicationManager()
        
    def _get_file_info(self, file_path):
        try:
            if not os.path.exists(file_path):
                return None
            
            mtime = os.path.getmtime(file_path)
            
            file_hash = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    file_hash.update(chunk)
            md5 = file_hash.hexdigest()
            
            return {
                'mtime': mtime,
                'md5': md5,
                'size': os.path.getsize(file_path)
            }
        except Exception as e:
            self._server_manager.logger.error(f"获取文件信息失败 {file_path}: {str(e)}")
            return None
    
    def _is_file_changed(self, file_path, old_info):
        if not old_info:
            return True
        
        new_info = self._get_file_info(file_path)
        if not new_info:
            return False
        
        if new_info['mtime'] != old_info['mtime']:
            if new_info['md5'] != old_info['md5']:
                return True
        return False
    
    def _validate_plugin_class(self, plugin_class, module_name):
        try:
            if not hasattr(plugin_class, 'handle_event_async'):
                self._server_manager.logger.error(f"插件 {module_name} 缺少必需的异步事件处理方法 'handle_event_async'")
                return False
            
            if not callable(getattr(plugin_class, 'handle_event_async', None)):
                self._server_manager.logger.error(f"插件 {module_name} 的 'handle_event_async' 方法不可调用")
                return False
            
            method = getattr(plugin_class, 'handle_event_async')
            if not asyncio.iscoroutinefunction(method):
                self._server_manager.logger.error(f"插件 {module_name} 的 'handle_event_async' 方法不是异步函数")
                return False
            
            if not hasattr(plugin_class, '__init__'):
                self._server_manager.logger.error(f"插件 {module_name} 缺少初始化方法 '__init__'")
                return False
            
            return True
        except Exception as e:
            self._server_manager.logger.error(f"验证插件类 {module_name} 时出错: {str(e)}")
            return False
    
    async def _log_error_once(self, plugin_name, error_msg, exc_info=False):
        error_hash = hashlib.md5(error_msg.encode('utf-8')).hexdigest()
        current_time = time.time()
        
        if error_hash in self.error_history:
            last_time = self.error_history[error_hash]
            if current_time - last_time < 3600:
                return False
        
        if exc_info:
            self._server_manager.logger.error(f"插件 {plugin_name} 出错: {error_msg}", exc_info=True)
        else:
            self._server_manager.logger.error(f"插件 {plugin_name} 出错: {error_msg}")
        
        self.error_history[error_hash] = current_time
        return True
    
    def _install_missing_modules(self, module_name, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            import_pattern = r'^(?:from|import)\s+(\w+)'
            imports = re.findall(import_pattern, content, re.MULTILINE)
            
            missing_modules = []
            for module in set(imports):
                if (module not in sys.builtin_module_names and 
                    not self._is_module_available(module) and 
                    module not in self.installed_modules):
                    missing_modules.append(module)
            
            if missing_modules:
                self._server_manager.logger.info(f"插件 {module_name} 需要安装以下模块: {', '.join(missing_modules)}")
                
                for module in missing_modules:
                    try:
                        self._server_manager.logger.info(f"正在安装模块: {module}")
                        
                        result = subprocess.run(
                            [sys.executable, "-m", "pip", "install", module],
                            capture_output=True,
                            text=True,
                            timeout=Config.MODULE_INSTALL_TIMEOUT
                        )
                        
                        if result.returncode == 0:
                            self._server_manager.logger.info(f"模块 {module} 安装成功")
                            self.installed_modules.add(module)
                        else:
                            self._server_manager.logger.error(f"模块 {module} 安装失败: {result.stderr}")
                            return False
                    except subprocess.TimeoutExpired:
                        self._server_manager.logger.error(f"安装模块 {module} 超时")
                        return False
                    except Exception as e:
                        self._server_manager.logger.error(f"安装模块 {module} 时出错: {str(e)}")
                        return False
            
            return True
        except Exception as e:
            self._server_manager.logger.error(f"检查模块依赖时出错: {str(e)}")
            return False

    def _is_module_available(self, module_name):
        return importlib.util.find_spec(module_name) is not None
    
    async def load_plugins(self):
        if not os.path.exists(self.plugins_dir):
            self._server_manager.logger.warning(f"插件目录不存在: {self.plugins_dir}")
            return
        
        if not self._plugin_path_inserted:
            if self.plugins_dir not in sys.path:
                sys.path.insert(0, self.plugins_dir)
            self._plugin_path_inserted = True
        
        async with self._lock:
            self.plugins = []
            self.plugin_files = {}
            self.plugin_modules = {}
        
        loaded_count = 0
        rejected_count = 0
        
        for filename in os.listdir(self.plugins_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                file_path = os.path.join(self.plugins_dir, filename)
                
                try:
                    file_info = self._get_file_info(file_path)
                    if file_info:
                        self.plugin_files[file_path] = file_info
                    
                    module_name = filename[:-3]
                    
                    if Config.AUTO_INSTALL_MODULES:
                        if not self._install_missing_modules(module_name, file_path):
                            self._server_manager.logger.error(f"插件 {module_name} 的依赖安装失败，拒绝加载")
                            rejected_count += 1
                            continue
                    
                    if module_name in sys.modules:
                        del sys.modules[module_name]
                    
                    module = importlib.import_module(module_name)
                    
                    self.plugin_modules[file_path] = module
                    
                    if hasattr(module, "Plugin"):
                        plugin_class = getattr(module, "Plugin")
                        if not self._validate_plugin_class(plugin_class, module_name):
                            self._server_manager.logger.error(f"插件 {module_name} 类验证失败，拒绝加载")
                            rejected_count += 1
                            continue
                        
                        plugin_state_accessor = PluginStateAccessor(module_name, global_state)
                        
                        context = PluginContext(module_name, readonly_global_state, plugin_state_accessor)
                        self.plugin_contexts[module_name] = context
                        
                        plugin = module.Plugin(context)
                        
                        if plugin:
                            async with self._lock:
                                self.plugins.append(plugin)
                            self._server_manager.logger.info(f"加载插件: {module_name}")
                            loaded_count += 1
                        else:
                            self._server_manager.logger.warning(f"插件 {module_name} 创建实例失败")
                            rejected_count += 1
                    else:
                        self._server_manager.logger.warning(f"插件 {module_name} 没有 Plugin 类，跳过加载")
                        rejected_count += 1
                        
                except ImportError as e:
                    if Config.AUTO_INSTALL_MODULES:
                        missing_module = str(e).split("'")[1]
                        self._server_manager.logger.info(f"检测到缺失模块: {missing_module}，正在安装...")
                        
                        try:
                            result = subprocess.run(
                                [sys.executable, "-m", "pip", "install", missing_module],
                                capture_output=True,
                                text=True,
                                timeout=Config.MODULE_INSTALL_TIMEOUT
                            )
                            
                            if result.returncode == 0:
                                self._server_manager.logger.info(f"模块 {missing_module} 安装成功")
                                self.installed_modules.add(missing_module)
                                
                                if module_name in sys.modules:
                                    del sys.modules[module_name]
                                
                                module = importlib.import_module(module_name)
                                
                                self.plugin_modules[file_path] = module
                                
                                if hasattr(module, "Plugin"):
                                    plugin_class = getattr(module, "Plugin")
                                    if not self._validate_plugin_class(plugin_class, module_name):
                                        self._server_manager.logger.error(f"插件 {module_name} 类验证失败，拒绝加载")
                                        rejected_count += 1
                                        continue
                                    
                                    plugin_state_accessor = PluginStateAccessor(module_name, global_state)
                                    
                                    context = PluginContext(module_name, readonly_global_state, plugin_state_accessor)
                                    self.plugin_contexts[module_name] = context
                                    
                                    plugin = module.Plugin(context)
                                    
                                    if plugin:
                                        async with self._lock:
                                            self.plugins.append(plugin)
                                        self._server_manager.logger.info(f"加载插件: {module_name}")
                                        loaded_count += 1
                                    else:
                                        self._server_manager.logger.warning(f"插件 {module_name} 创建实例失败")
                                        rejected_count += 1
                                else:
                                    self._server_manager.logger.warning(f"插件 {module_name} 没有 Plugin 类，跳过加载")
                                    rejected_count += 1
                            else:
                                self._server_manager.logger.error(f"模块 {missing_module} 安装失败: {result.stderr}")
                                rejected_count += 1
                        except subprocess.TimeoutExpired:
                            self._server_manager.logger.error(f"安装模块 {missing_module} 超时")
                            rejected_count += 1
                        except Exception as install_error:
                            self._server_manager.logger.error(f"安装模块 {missing_module} 时出错: {str(install_error)}")
                            rejected_count += 1
                    else:
                        error_msg = f"加载插件 {filename} 失败: {str(e)}"
                        await self._log_error_once(filename, error_msg, Config.ENABLE_DEBUG)
                        rejected_count += 1
                except Exception as e:
                    error_msg = f"加载插件 {filename} 失败: {str(e)}"
                    await self._log_error_once(filename, error_msg, Config.ENABLE_DEBUG)
                    rejected_count += 1
        
        global_state._update_plugin_stats(loaded_count=loaded_count, rejected_count=rejected_count)
        
        self._server_manager.logger.info(f"插件加载完成: 成功 {loaded_count} 个, 失败 {rejected_count} 个")
        gc.collect()
        self.initial_loading_complete = True
    
    async def _force_cleanup_plugin(self, plugin_name):
        if plugin_name in self.plugin_contexts:
            context = self.plugin_contexts[plugin_name]
            
            for task in context.active_tasks:
                if not task.done():
                    task.cancel()
            
            if context.active_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*context.active_tasks, return_exceptions=True),
                        timeout=Config.PLUGIN_CANCEL_WAIT_TIMEOUT  
                    )
                except asyncio.TimeoutError:
                    self._server_manager.logger.warning(f"插件 {plugin_name} 的活动任务清理超时，强制跳过")
            
            for handler in context.logger.handlers[:]:
                handler.close()
                context.logger.removeHandler(handler)
            
            logging.Logger.manager.loggerDict.pop(f"plugin.{plugin_name}", None)
            del self.plugin_contexts[plugin_name]
            
            self.log_cleaner.clean_plugin_log_file(plugin_name)
            
            self._server_manager.logger.debug(f"已强制清理插件 {plugin_name} 的所有资源")
    
    async def reload_plugin(self, file_path):
        try:
            filename = os.path.basename(file_path)
            module_name = filename[:-3]
            
            if Config.AUTO_INSTALL_MODULES:
                if not self._install_missing_modules(module_name, file_path):
                    self._server_manager.logger.error(f"插件 {module_name} 的依赖安装失败，跳过重新加载")
                    return False
            
            await self._force_cleanup_plugin(module_name)
            
            if module_name in sys.modules:
                del sys.modules[module_name]
            
            module = importlib.import_module(module_name)
            
            self.plugin_modules[file_path] = module
            
            async with self._lock:
                for i, plugin in enumerate(self.plugins[:]):
                    if type(plugin).__module__ == module_name:
                        self.plugins.pop(i)
            
            if hasattr(module, "Plugin"):
                plugin_state_accessor = PluginStateAccessor(module_name, global_state)
                
                context = PluginContext(module_name, readonly_global_state, plugin_state_accessor)
                self.plugin_contexts[module_name] = context
                
                plugin = module.Plugin(context)
                
                if plugin:
                    async with self._lock:
                        self.plugins.append(plugin)
                    
                    current_reload_count = global_state.get_global_var("framework.plugins.reload_count", 0)
                    global_state._update_plugin_stats(reload_count=current_reload_count + 1)
                    
                    self._server_manager.logger.info(f"重新加载插件: {module_name}")
                    return True
                else:
                    return False
            else:
                self._server_manager.logger.warning(f"插件 {module_name} 没有正确的类，跳过重新加载")
                return False
        except Exception as e:
            error_msg = f"重新加载插件 {filename} 失败: {str(e)}"
            await self._log_error_once(filename, error_msg, Config.ENABLE_DEBUG)
        
        gc.collect()
        return False

    async def reload_plugin_by_name(self, plugin_name):
        for file_path, module in self.plugin_modules.items():
            module_name = os.path.basename(file_path)[:-3]
            if module_name == plugin_name:
                return await self.reload_plugin(file_path)
        
        self._server_manager.logger.error(f"未找到插件: {plugin_name}")
        return False

    async def unload_plugin_by_name(self, plugin_name):
        async with self._lock:
            for i, plugin in enumerate(self.plugins[:]):
                if type(plugin).__module__ == plugin_name:
                    await self._force_cleanup_plugin(plugin_name)
                    self.plugins.pop(i)
                    
                    if plugin_name in sys.modules:
                        del sys.modules[plugin_name]
                    
                    current_loaded_count = global_state.get_global_var("framework.plugins.loaded_count", 0)
                    global_state._update_plugin_stats(loaded_count=current_loaded_count - 1)
                    
                    self._server_manager.logger.info(f"已卸载插件: {plugin_name}")
                    return True
        
        self._server_manager.logger.error(f"未找到插件: {plugin_name}")
        return False

    async def load_single_plugin(self, file_path):
        if not os.path.exists(file_path):
            self._server_manager.logger.error(f"插件文件不存在: {file_path}")
            return False
        
        filename = os.path.basename(file_path)
        if not filename.endswith(".py") or filename == "__init__.py":
            self._server_manager.logger.error(f"无效的插件文件: {file_path}")
            return False
        
        return await self.reload_plugin(file_path)
    
    async def check_for_new_plugins(self):
        if not os.path.exists(self.plugins_dir):
            return False
        
        new_plugins_found = False
        current_files = set()
        
        for filename in os.listdir(self.plugins_dir):
            if filename.endswith(".py") and filename != "__init__.py":
                file_path = os.path.join(self.plugins_dir, filename)
                current_files.add(file_path)
                
                if file_path not in self.plugin_files:
                    try:
                        module_name = filename[:-3]
                        
                        if Config.AUTO_INSTALL_MODULES:
                            if not self._install_missing_modules(module_name, file_path):
                                self._server_manager.logger.error(f"插件 {module_name} 的依赖安装失败，跳过加载")
                                continue
                        
                        if module_name in sys.modules:
                            del sys.modules[module_name]
                        
                        module = importlib.import_module(module_name)
                        
                        file_info = self._get_file_info(file_path)
                        if file_info:
                            self.plugin_files[file_path] = file_info
                        
                        self.plugin_modules[file_path] = module
                        
                        if hasattr(module, "Plugin"):
                            plugin_state_accessor = PluginStateAccessor(module_name, global_state)
                            
                            context = PluginContext(module_name, readonly_global_state, plugin_state_accessor)
                            self.plugin_contexts[module_name] = context
                            
                            plugin = module.Plugin(context)
                            
                            if plugin:
                                async with self._lock:
                                    self.plugins.append(plugin)
                                
                                current_loaded_count = global_state.get_global_var("framework.plugins.loaded_count", 0)
                                global_state._update_plugin_stats(loaded_count=current_loaded_count + 1)
                                
                                self._server_manager.logger.info(f"发现并加载新插件: {module_name}")
                                new_plugins_found = True
                    except ImportError as e:
                        if Config.AUTO_INSTALL_MODULES:
                            missing_module = str(e).split("'")[1]
                            self._server_manager.logger.info(f"检测到缺失模块: {missing_module}，正在安装...")
                            
                            try:
                                result = subprocess.run(
                                    [sys.executable, "-m", "pip", "install", missing_module],
                                    capture_output=True,
                                    text=True,
                                    timeout=Config.MODULE_INSTALL_TIMEOUT
                                )
                                
                                if result.returncode == 0:
                                    self._server_manager.logger.info(f"模块 {missing_module} 安装成功")
                                    self.installed_modules.add(missing_module)
                                    
                                    if module_name in sys.modules:
                                        del sys.modules[module_name]
                                    
                                    module = importlib.import_module(module_name)
                                    
                                    self.plugin_modules[file_path] = module
                                    
                                    if hasattr(module, "Plugin"):
                                        plugin_state_accessor = PluginStateAccessor(module_name, global_state)
                                        
                                        context = PluginContext(module_name, readonly_global_state, plugin_state_accessor)
                                        self.plugin_contexts[module_name] = context
                                        
                                        plugin = module.Plugin(context)
                                        
                                        if plugin:
                                            async with self._lock:
                                                self.plugins.append(plugin)
                                            
                                            current_loaded_count = global_state.get_global_var("framework.plugins.loaded_count", 0)
                                            global_state._update_plugin_stats(loaded_count=current_loaded_count + 1)
                                            
                                            self._server_manager.logger.info(f"发现并加载新插件: {module_name}")
                                            new_plugins_found = True
                                else:
                                    self._server_manager.logger.error(f"模块 {missing_module} 安装失败: {result.stderr}")
                            except subprocess.TimeoutExpired:
                                self._server_manager.logger.error(f"安装模块 {missing_module} 超时")
                            except Exception as install_error:
                                self._server_manager.logger.error(f"安装模块 {missing_module} 时出错: {str(install_error)}")
                        else:
                            error_msg = f"加载新插件 {module_name} 失败: {str(e)}"
                            await self._log_error_once(module_name, error_msg, Config.ENABLE_DEBUG)
                    except Exception as e:
                        error_msg = f"加载新插件 {module_name} 失败: {str(e)}"
                        await self._log_error_once(module_name, error_msg, Config.ENABLE_DEBUG)
        
        for file_path in list(self.plugin_files.keys()):
            if file_path not in current_files:
                filename = os.path.basename(file_path)
                module_name = filename[:-3]
                
                await self._force_cleanup_plugin(module_name)
                
                async with self._lock:
                    for i, plugin in enumerate(self.plugins[:]):
                        if type(plugin).__module__ == module_name:
                            self.plugins.pop(i)
                            
                            current_loaded_count = global_state.get_global_var("framework.plugins.loaded_count", 0)
                            global_state._update_plugin_stats(loaded_count=current_loaded_count - 1)
                            
                            self._server_manager.logger.info(f"插件 {module_name} 已被移除")
                
                if module_name in sys.modules:
                    del sys.modules[module_name]
                
                if file_path in self.plugin_files:
                    del self.plugin_files[file_path]
                if file_path in self.plugin_modules:
                    del self.plugin_modules[file_path]
        
        if new_plugins_found:
            gc.collect()
            
        return new_plugins_found
    
    async def check_for_updates(self):
        if not Config.HOT_RELOAD:
            return
        
        user_plugins_updated = await self.check_for_new_plugins()
        
        if user_plugins_updated:
            self._server_manager.logger.info("发现新用户插件，已加载")
        
        for file_path, old_info in list(self.plugin_files.items()):
            if not os.path.exists(file_path):
                continue
                
            if self._is_file_changed(file_path, old_info):
                self._server_manager.logger.info(f"检测到插件文件修改: {os.path.basename(file_path)}")
                if await self.reload_plugin(file_path):
                    new_info = self._get_file_info(file_path)
                    if new_info:
                        self.plugin_files[file_path] = new_info
    
    async def handle_event(self, event):
        if self.deduplication_manager.check_event(event):
            if Config.ENABLE_DEBUG:
                self._server_manager.logger.debug(f"检测到重复事件，已跳过处理")
            return
        
        if self.startup_rejector.should_reject_event():
            remaining_time = self.startup_rejector.get_remaining_time()
            if Config.ENABLE_DEBUG:
                self._server_manager.logger.debug(f"启动拒绝期内，拒绝处理事件。剩余时间: {remaining_time:.1f}秒，已拒绝事件数: {self.startup_rejector.rejected_count}")
            return
        
        current_events = global_state.get_global_var("framework.runtime.total_events_processed", 0)
        global_state._update_runtime_stats(
            total_events=current_events + 1,
            last_event_time=datetime.now().isoformat()
        )
        
        async with self._lock:
            plugins_copy = self.plugins[:]
        
        timeout_tracker = {}
        user_tasks = []
        
        for plugin in plugins_copy:
            plugin_name = type(plugin).__module__
            task = asyncio.create_task(self._handle_plugin_event_with_timeout(plugin, event, plugin_name))
            user_tasks.append(task)
            timeout_tracker[task] = plugin_name
        
        try:
            done, pending = await asyncio.wait(user_tasks, timeout=Config.PLUGIN_EVENT_TIMEOUT)
            
            for task in pending:
                plugin_name = timeout_tracker.get(task, "unknown")
                self._server_manager.logger.warning(f"插件 {plugin_name} 事件处理超时，正在处理...")
                
                task.cancel()
                
                global_state._increment_plugin_timeout()
                
                try:
                    await asyncio.wait_for(task, timeout=Config.PLUGIN_CANCEL_WAIT_TIMEOUT)
                    self._server_manager.logger.info(f"插件 {plugin_name} 已成功取消")
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    self._server_manager.logger.error(f"插件 {plugin_name} 拒绝终止，将强制重载插件")
                    asyncio.create_task(self.reload_plugin_by_name(plugin_name))
            
            for task in done:
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass  
                except Exception as e:
                    plugin_name = timeout_tracker.get(task, "unknown")
                    self._server_manager.logger.error(f"插件 {plugin_name} 事件处理出错: {str(e)}")
                    
        except Exception as e:
            self._server_manager.logger.error(f"用户插件事件处理出错: {str(e)}")
    
    async def _handle_plugin_event_with_timeout(self, plugin, event, plugin_name):
        try:
            if hasattr(plugin, 'handle_event_async'):
                task = asyncio.create_task(plugin.handle_event_async(event))
                if plugin_name in self.plugin_contexts:
                    self.plugin_contexts[plugin_name].register_task(task)
                await task
            else:
                self._server_manager.logger.warning(f"插件 {plugin_name} 没有实现异步事件处理方法 handle_event_async，将忽略此事件")
        except Exception as e:
            error_msg = f"插件 {plugin_name} 处理事件出错: {str(e)}"
            await self._log_error_once(plugin_name, error_msg, Config.ENABLE_DEBUG)

class BotApplication:
    def __init__(self, logger, api_logger):
        self.logger = logger
        self.api_logger = api_logger
        self.global_stop_event = asyncio.Event()
        
        self.server_manager = ServerManager(Config, self.logger)
        
        self.plugin_manager = PluginManager(self.server_manager)
        
        self._initialize_global_state()
    
    def _initialize_global_state(self):
        global_state._set_global_var("framework.name", "BotFramework")
        global_state._set_global_var("framework.config", {
            "api_base_url": Config.API_BASE_URL,
            "event_server_host": Config.EVENT_SERVER_HOST,
            "event_server_port": Config.EVENT_SERVER_PORT,
            "plugins_dir": Config.PLUGINS_DIR,
            "hot_reload": Config.HOT_RELOAD,
            "enable_debug": Config.ENABLE_DEBUG
        })
    
    async def initialize(self):
        await self.server_manager.initialize()
        
        global_state._update_framework_status("running")
        
        self.logger.info("框架初始化完成")
    
    async def shutdown(self):
        global_state._update_framework_status("shutting_down")
        
        self.logger.info("正在关闭服务器...")
        
        await self.server_manager.shutdown()
        self.global_stop_event.set()
    
    async def hot_reload_worker(self):
        self.logger.info("热重载任务已启动")
        
        while not self.global_stop_event.is_set():
            try:
                await self.plugin_manager.check_for_updates()
                
                global_state._update_system_status(last_reload=datetime.now().isoformat())
            except Exception as e:
                self.logger.error(f"热重载检查出错: {str(e)}", exc_info=Config.ENABLE_DEBUG)
            
            await asyncio.sleep(Config.HOT_RELOAD_INTERVAL)
    
    async def config_watch_worker(self):
        self.logger.info("配置监控任务已启动")
        
        config_file = os.path.join(os.path.dirname(__file__), "config.py")
        last_mtime = os.path.getmtime(config_file) if os.path.exists(config_file) else 0
        
        while not self.global_stop_event.is_set():
            try:
                if os.path.exists(config_file):
                    current_mtime = os.path.getmtime(config_file)
                    if current_mtime > last_mtime:
                        self.logger.info("检测到配置文件修改，重新加载配置")
                        
                        if "config" in sys.modules:
                            importlib.reload(sys.modules["config"])
                            global Config
                            from config import Config
                            
                            self.logger.setLevel(Config.LOG_LEVEL)
                            
                            if Config.ENABLE_DEBUG:
                                self.logger.setLevel(logging.DEBUG)
                                logging.getLogger('aiohttp').setLevel(logging.INFO)
                                logging.getLogger('asyncio').setLevel(logging.INFO)
                                self.logger.debug("调试模式已启用，将记录详细日志信息")
                            else:
                                logging.getLogger('aiohttp').setLevel(logging.WARNING)
                                logging.getLogger('asyncio').setLevel(logging.WARNING)
                            
                            self.logger.info("配置重新加载成功")
                        
                        last_mtime = current_mtime
            except Exception as e:
                self.logger.error(f"配置监控出错: {str(e)}", exc_info=Config.ENABLE_DEBUG)
            
            await asyncio.sleep(10)
    
    async def log_cleanup_worker(self):
        self.logger.info("日志清理任务已启动")
        
        while not self.global_stop_event.is_set():
            try:
                self.plugin_manager.log_cleaner.clean_runtime_logs()
                
                global_state._update_system_status(last_cleanup=datetime.now().isoformat())
            except Exception as e:
                self.logger.error(f"日志清理出错: {str(e)}", exc_info=Config.ENABLE_DEBUG)
            
            await asyncio.sleep(86400)  
    
    async def runtime_stats_worker(self):
        """运行时统计更新任务"""
        self.logger.info("运行时统计任务已启动")
        
        start_time = time.time()
        
        while not self.global_stop_event.is_set():
            try:
                uptime = time.time() - start_time
                
                global_state._update_runtime_stats(uptime=uptime)
                
            except Exception as e:
                self.logger.error(f"运行时统计更新出错: {str(e)}", exc_info=Config.ENABLE_DEBUG)
            
            await asyncio.sleep(10)
    
    async def handle_event(self, request):
        try:
            data = await request.json()
            if Config.ENABLE_DEBUG:
                self.logger.debug(f"收到事件: {json.dumps(data, ensure_ascii=False, indent=2)}")
            
            post_type = data.get("post_type", "unknown")
            
            asyncio.create_task(self.plugin_manager.handle_event(data))
            
            return web.json_response({})
        
        except Exception as e:
            self.logger.error(f"处理事件时出错: {str(e)}", exc_info=Config.ENABLE_DEBUG)
            return web.json_response({"error": str(e)}, status=500)
    
    async def run_server(self):
        self.logger.info("框架启动完成")
        
        status_display = self.plugin_manager.startup_rejector.get_status_display()
        self.logger.info(status_display)
        
        await self.plugin_manager.load_plugins()
        
        if Config.HOT_RELOAD:
            reload_task = asyncio.create_task(self.hot_reload_worker())
            self.server_manager.register_task(reload_task)
            self.logger.info("热重载功能已启用")
        
        config_task = asyncio.create_task(self.config_watch_worker())
        self.server_manager.register_task(config_task)
        self.logger.info("配置监控功能已启用")
        
        log_cleanup_task = asyncio.create_task(self.log_cleanup_worker())
        self.server_manager.register_task(log_cleanup_task)
        self.logger.info("日志清理功能已启用")
        
        runtime_stats_task = asyncio.create_task(self.runtime_stats_worker())
        self.server_manager.register_task(runtime_stats_task)
        self.logger.info("运行时统计功能已启用")
        
        await self.server_manager.start_all()
        
        app = web.Application()
        app.router.add_post('/onebot', self.handle_event)
        
        self.logger.info(f"启动事件接收服务器: {Config.EVENT_SERVER_HOST}:{Config.EVENT_SERVER_PORT}")
        
        try:
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, Config.EVENT_SERVER_HOST, Config.EVENT_SERVER_PORT)
            await site.start()
            
            self.logger.info("服务器启动完成∗︎˚(* ˃̤൬˂̤ *)˚∗︎")
            
            await self.global_stop_event.wait()
            
        except Exception as e:
            self.logger.critical(f"服务器启动失败: {str(e)}", exc_info=True)
        finally:
            await self.shutdown()