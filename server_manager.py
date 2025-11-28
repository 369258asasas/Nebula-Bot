import os
import sys
import time
import threading
import shutil
import atexit
import signal
import subprocess
import gc
import importlib
import asyncio
import aiohttp
from datetime import datetime
import logging
from config import Config

class PluginContext:
    def __init__(self, plugin_name, global_state, plugin_state_accessor):
        self.plugin_name = plugin_name
        self.global_state = global_state  
        self.shared = plugin_state_accessor 
        self.logger = self._setup_logger(plugin_name)
        self.active_tasks = set()
        
    def _setup_logger(self, plugin_name):
        logger = logging.getLogger(f"plugin.{plugin_name}")
        logger.setLevel(Config.LOG_LEVEL)
        
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(base_dir, 'logs')
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
        
        log_filename = os.path.join(logs_dir, f"plugin_{plugin_name}.log")
        
        file_handler = logging.FileHandler(log_filename, 'a', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.propagate = False
        
        return logger
    
    def register_task(self, task):
        self.active_tasks.add(task)
        task.add_done_callback(lambda t: self.active_tasks.discard(t))
    
    async def cleanup(self):
        for task in self.active_tasks:
            if not task.done():
                task.cancel()
        
        if self.active_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.active_tasks, return_exceptions=True),
                    timeout=Config.PLUGIN_CANCEL_WAIT_TIMEOUT
                )
            except asyncio.TimeoutError:
                pass
        
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
        
        plugin_logger_name = f"plugin.{self.plugin_name}"
        if plugin_logger_name in logging.Logger.manager.loggerDict:
            del logging.Logger.manager.loggerDict[plugin_logger_name]

class ConnectionPool:
    def __init__(self, max_connections=100):
        self.max_connections = max_connections
        self.connector = None
        self.session = None
        self._lock = threading.Lock()
        
    async def init_pool(self):
        connector = aiohttp.TCPConnector(
            limit=self.max_connections,
            limit_per_host=10,
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        self.session = aiohttp.ClientSession(connector=connector)
        return self.session
    
    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

class RequestQueue:
    def __init__(self, max_queue_size=1000, max_workers=10):
        self.max_queue_size = max_queue_size
        self.max_workers = max_workers
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.workers = []
        self.is_running = False
        
    async def start(self):
        self.is_running = True
        self.workers = [
            asyncio.create_task(self._worker())
            for _ in range(self.max_workers)
        ]
    
    async def stop(self):
        self.is_running = False
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers = []
    
    async def add_request(self, coro, callback=None):
        await self.queue.put((coro, callback))
    
    async def _worker(self):
        while self.is_running:
            try:
                coro, callback = await self.queue.get()
                try:
                    result = await coro
                    if callback:
                        await callback(result)
                except Exception as e:
                    if callback:
                        await callback(None, e)
                finally:
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                continue

class ServerManager:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self._stop_event = asyncio.Event()
        self.tasks = []
        self.connection_pool = ConnectionPool()
        self.request_queue = RequestQueue()
        self.installed_modules = set()
        
    async def initialize(self):
        await self.connection_pool.init_pool()
        await self.request_queue.start()
        
    async def shutdown(self):
        self._stop_event.set()
        await self.request_queue.stop()
        await self.connection_pool.close()
        
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        await asyncio.gather(*self.tasks, return_exceptions=True)
    
    def cleanup_pycache(self):
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            pycache_dir = os.path.join(base_dir, "__pycache__")
            if os.path.exists(pycache_dir):
                shutil.rmtree(pycache_dir)
            
            plugins_dir = os.path.join(base_dir, self.config.PLUGINS_DIR)
            pycache_dir = os.path.join(plugins_dir, "__pycache__")
            if os.path.exists(pycache_dir):
                shutil.rmtree(pycache_dir)
        except Exception:
            pass
    
    async def graceful_shutdown(self, signum=None, frame=None):
        self.logger.info("收到关闭信号，正在关闭服务器...")
        self._stop_event.set()
        self.cleanup_pycache()
        os._exit(0)
    
    def register_task(self, task):
        self.tasks.append(task)
    
    async def start_all(self):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.graceful_shutdown()))
        
        atexit.register(self.cleanup_pycache)
        
        self.logger.info("服务器管理器已启动")
    
    async def stop_all(self):
        await self.shutdown()
    
    def is_module_available(self, module_name):
        return importlib.util.find_spec(module_name) is not None
    
    def _install_missing_modules(self, module_name, file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            import_pattern = r'^(?:from|import)\s+(\w+)'
            imports = re.findall(import_pattern, content, re.MULTILINE)
            
            missing_modules = []
            for module in set(imports):
                if (module not in sys.builtin_module_names and 
                    not self.is_module_available(module) and 
                    module not in self.installed_modules):
                    missing_modules.append(module)
            
            if missing_modules:
                self.logger.info(f"安装模块: {', '.join(missing_modules)}")
                
                for module in missing_modules:
                    try:
                        result = subprocess.run(
                            [sys.executable, "-m", "pip", "install", module],
                            capture_output=True,
                            text=True,
                            timeout=self.config.MODULE_INSTALL_TIMEOUT
                        )
                        
                        if result.returncode == 0:
                            self.installed_modules.add(module)
                        else:
                            self.logger.error(f"模块 {module} 安装失败")
                            return False
                    except subprocess.TimeoutExpired:
                        self.logger.error(f"安装模块 {module} 超时")
                        return False
                    except Exception as e:
                        self.logger.error(f"安装模块时出错: {str(e)}")
                        return False
            
            return True
        except Exception as e:
            self.logger.error(f"检查模块依赖时出错: {str(e)}")
            return False