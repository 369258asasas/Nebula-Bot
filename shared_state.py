import threading
import time
import json
import hashlib
from typing import Dict, Any, Optional, List, Set
from datetime import datetime
from config import Config

class GlobalState:
    """框架全局状态，包含框架状态和插件共享状态"""
    def __init__(self):
        self._lock = threading.RLock()
        self._global_vars: Dict[str, Any] = {}
        self._plugin_vars: Dict[str, Dict[str, Any]] = {}  
        self._access_control: Dict[str, Set[str]] = {}     
        self._access_log: List[Dict[str, Any]] = []
        self._max_log_entries = 1000
        self._security_hashes: Dict[str, str] = {}
        self._value_hashes: Dict[str, str] = {}  
        
        self._initialize_framework_vars()
    
    def _initialize_framework_vars(self):
        with self._lock:
            self._global_vars["framework.version"] = Config.get_current_version()
            self._global_vars["framework.start_time"] = datetime.now().isoformat()
            self._global_vars["framework.status"] = "initializing"
            
            self._global_vars["framework.plugins.loaded_count"] = 0
            self._global_vars["framework.plugins.rejected_count"] = 0
            self._global_vars["framework.plugins.timeout_count"] = 0
            self._global_vars["framework.plugins.reload_count"] = 0
            
            self._global_vars["framework.runtime.active_background_tasks"] = 0
            self._global_vars["framework.runtime.total_events_processed"] = 0
            self._global_vars["framework.runtime.last_event_time"] = None
            self._global_vars["framework.runtime.uptime_seconds"] = 0
            
            self._global_vars["framework.performance.api_requests_total"] = 0
            self._global_vars["framework.performance.api_requests_failed"] = 0
            self._global_vars["framework.performance.plugin_timeouts"] = 0
            
            self._global_vars["framework.system.last_cleanup_time"] = None
            self._global_vars["framework.system.last_reload_check"] = None
            self._global_vars["framework.system.is_healthy"] = True
            
            for key in self._global_vars:
                self._security_hashes[key] = self._calculate_value_hash(self._global_vars[key])
                self._value_hashes[key] = self._calculate_value_hash(self._global_vars[key])
    
    def _calculate_value_hash(self, value: Any) -> str:
        try:
            value_str = json.dumps(value, sort_keys=True, default=str)
            return hashlib.sha256(value_str.encode()).hexdigest()
        except:
            return "unknown"
    
    def _verify_value_integrity(self, key: str, value: Any) -> bool:
        """验证值完整性"""
        current_hash = self._calculate_value_hash(value)
        stored_hash = self._value_hashes.get(key)
        return current_hash == stored_hash
    
    def get_global_var(self, key: str, default: Any = None) -> Any:
        with self._lock:
            value = self._global_vars.get(key, default)
            if key in self._value_hashes and not self._verify_value_integrity(key, value):
                raise SecurityError(f"值完整性校验失败: {key}")
            return value
    
    def get_all_global_vars(self) -> Dict[str, Any]:
        with self._lock:
            result = self._global_vars.copy()
            for key in result:
                if key in self._value_hashes and not self._verify_value_integrity(key, result[key]):
                    raise SecurityError(f"值完整性校验失败: {key}")
            return result
    
    def get_framework_summary(self) -> Dict[str, Any]:
        """获取框架摘要信息"""
        return {
            "version": self.get_global_var("framework.version"),
            "status": self.get_global_var("framework.status"),
            "start_time": self.get_global_var("framework.start_time"),
            "uptime_seconds": self.get_global_var("framework.runtime.uptime_seconds"),
            "plugins_loaded": self.get_global_var("framework.plugins.loaded_count"),
            "plugins_rejected": self.get_global_var("framework.plugins.rejected_count"),
            "plugin_timeouts": self.get_global_var("framework.plugins.timeout_count"),
            "active_background_tasks": self.get_global_var("framework.runtime.active_background_tasks"),
            "total_events_processed": self.get_global_var("framework.runtime.total_events_processed"),
            "api_requests_total": self.get_global_var("framework.performance.api_requests_total"),
            "api_requests_failed": self.get_global_var("framework.performance.api_requests_failed"),
            "is_healthy": self.get_global_var("framework.system.is_healthy")
        }
    
    def _set_global_var(self, key: str, value: Any):
        """框架内部使用的设置方法，插件无法调用"""
        with self._lock:
            self._global_vars[key] = value
            self._security_hashes[key] = self._calculate_value_hash(value)
            self._value_hashes[key] = self._calculate_value_hash(value)
    
    def _update_framework_status(self, status: str):
        """更新框架状态 - 仅框架内部使用"""
        self._set_global_var("framework.status", status)
    
    def _update_plugin_stats(self, loaded_count: int = None, rejected_count: int = None, 
                           timeout_count: int = None, reload_count: int = None):
        """更新插件统计信息 - 仅框架内部使用"""
        with self._lock:
            if loaded_count is not None:
                self._global_vars["framework.plugins.loaded_count"] = loaded_count
            if rejected_count is not None:
                self._global_vars["framework.plugins.rejected_count"] = rejected_count
            if timeout_count is not None:
                self._global_vars["framework.plugins.timeout_count"] = timeout_count
            if reload_count is not None:
                self._global_vars["framework.plugins.reload_count"] = reload_count
            
            for key in ["framework.plugins.loaded_count", "framework.plugins.rejected_count", 
                       "framework.plugins.timeout_count", "framework.plugins.reload_count"]:
                if key in self._global_vars:
                    self._security_hashes[key] = self._calculate_value_hash(self._global_vars[key])
                    self._value_hashes[key] = self._calculate_value_hash(self._global_vars[key])
    
    def _update_runtime_stats(self, active_tasks: int = None, total_events: int = None, 
                            last_event_time: str = None, uptime: float = None):
        """更新运行时统计 - 仅框架内部使用"""
        with self._lock:
            if active_tasks is not None:
                self._global_vars["framework.runtime.active_background_tasks"] = active_tasks
            if total_events is not None:
                self._global_vars["framework.runtime.total_events_processed"] = total_events
            if last_event_time is not None:
                self._global_vars["framework.runtime.last_event_time"] = last_event_time
            if uptime is not None:
                self._global_vars["framework.runtime.uptime_seconds"] = uptime
            
            for key in ["framework.runtime.active_background_tasks", "framework.runtime.total_events_processed",
                       "framework.runtime.last_event_time", "framework.runtime.uptime_seconds"]:
                if key in self._global_vars:
                    self._security_hashes[key] = self._calculate_value_hash(self._global_vars[key])
                    self._value_hashes[key] = self._calculate_value_hash(self._global_vars[key])
    
    def _update_performance_stats(self, api_requests_total: int = None, api_requests_failed: int = None,
                               plugin_timeouts: int = None):
        """更新性能统计 - 仅框架内部使用"""
        with self._lock:
            if api_requests_total is not None:
                self._global_vars["framework.performance.api_requests_total"] = api_requests_total
            if api_requests_failed is not None:
                self._global_vars["framework.performance.api_requests_failed"] = api_requests_failed
            if plugin_timeouts is not None:
                self._global_vars["framework.performance.plugin_timeouts"] = plugin_timeouts
            
            for key in ["framework.performance.api_requests_total", "framework.performance.api_requests_failed",
                       "framework.performance.plugin_timeouts"]:
                if key in self._global_vars:
                    self._security_hashes[key] = self._calculate_value_hash(self._global_vars[key])
                    self._value_hashes[key] = self._calculate_value_hash(self._global_vars[key])
    
    def _update_system_status(self, last_cleanup: str = None, last_reload: str = None, 
                           is_healthy: bool = None):
        """更新系统状态 - 仅框架内部使用"""
        with self._lock:
            if last_cleanup is not None:
                self._global_vars["framework.system.last_cleanup_time"] = last_cleanup
            if last_reload is not None:
                self._global_vars["framework.system.last_reload_check"] = last_reload
            if is_healthy is not None:
                self._global_vars["framework.system.is_healthy"] = is_healthy
            
            for key in ["framework.system.last_cleanup_time", "framework.system.last_reload_check",
                       "framework.system.is_healthy"]:
                if key in self._global_vars:
                    self._security_hashes[key] = self._calculate_value_hash(self._global_vars[key])
                    self._value_hashes[key] = self._calculate_value_hash(self._global_vars[key])
    
    def _increment_plugin_timeout(self):
        """增加插件超时计数 - 仅框架内部使用"""
        with self._lock:
            current = self._global_vars.get("framework.plugins.timeout_count", 0)
            self._global_vars["framework.plugins.timeout_count"] = current + 1
            self._security_hashes["framework.plugins.timeout_count"] = self._calculate_value_hash(current + 1)
            self._value_hashes["framework.plugins.timeout_count"] = self._calculate_value_hash(current + 1)
    
    def _increment_api_requests(self, success: bool = True):
        """增加API请求计数 - 仅框架内部使用"""
        with self._lock:
            total = self._global_vars.get("framework.performance.api_requests_total", 0)
            self._global_vars["framework.performance.api_requests_total"] = total + 1
            self._security_hashes["framework.performance.api_requests_total"] = self._calculate_value_hash(total + 1)
            self._value_hashes["framework.performance.api_requests_total"] = self._calculate_value_hash(total + 1)
            
            if not success:
                failed = self._global_vars.get("framework.performance.api_requests_failed", 0)
                self._global_vars["framework.performance.api_requests_failed"] = failed + 1
                self._security_hashes["framework.performance.api_requests_failed"] = self._calculate_value_hash(failed + 1)
                self._value_hashes["framework.performance.api_requests_failed"] = self._calculate_value_hash(failed + 1)

    
    def register_plugin(self, plugin_name: str):
        """注册插件"""
        with self._lock:
            if plugin_name not in self._plugin_vars:
                self._plugin_vars[plugin_name] = {}
                self._access_control[plugin_name] = set()
    
    def set_plugin_var(self, plugin_name: str, key: str, value: Any):
        """插件设置自己的变量"""
        with self._lock:
            if plugin_name not in self._plugin_vars:
                self.register_plugin(plugin_name)
            self._plugin_vars[plugin_name][key] = value
    
    def get_plugin_var(self, plugin_name: str, key: str, default: Any = None) -> Any:
        """插件获取自己的变量"""
        with self._lock:
            if plugin_name not in self._plugin_vars:
                return default
            return self._plugin_vars[plugin_name].get(key, default)
    
    def get_other_plugin_var(self, requester_plugin: str, target_plugin: str, key: str, default: Any = None) -> Any:
        """插件获取其他插件的变量（需要授权）"""
        with self._lock:
            if (target_plugin not in self._plugin_vars or 
                requester_plugin not in self._access_control[target_plugin]):
                return default
            return self._plugin_vars[target_plugin].get(key, default)
    
    def grant_access(self, plugin_name: str, authorized_plugin: str):
        """授权其他插件访问本插件的变量"""
        with self._lock:
            if plugin_name in self._access_control:
                self._access_control[plugin_name].add(authorized_plugin)
    
    def revoke_access(self, plugin_name: str, authorized_plugin: str):
        """撤销其他插件访问本插件的变量"""
        with self._lock:
            if plugin_name in self._access_control:
                self._access_control[plugin_name].discard(authorized_plugin)
    
    def get_plugin_vars(self, plugin_name: str) -> Dict[str, Any]:
        """获取插件的所有变量"""
        with self._lock:
            if plugin_name not in self._plugin_vars:
                return {}
            return self._plugin_vars[plugin_name].copy()
    
    def delete_plugin_var(self, plugin_name: str, key: str) -> bool:
        """删除插件的变量"""
        with self._lock:
            if plugin_name not in self._plugin_vars:
                return False
            if key in self._plugin_vars[plugin_name]:
                del self._plugin_vars[plugin_name][key]
                return True
            return False
    
    def clear_plugin_vars(self, plugin_name: str):
        """清空插件的所有变量"""
        with self._lock:
            if plugin_name in self._plugin_vars:
                self._plugin_vars[plugin_name].clear()

class SecurityError(Exception):
    """安全错误异常"""
    pass

global_state = GlobalState()

class ReadOnlyGlobalState:
    """只读全局状态，插件只能读取"""
    def __init__(self, global_state: GlobalState):
        self._global_state = global_state
    
    def get_global_var(self, key: str, default: Any = None) -> Any:
        return self._global_state.get_global_var(key, default)
    
    def get_all_global_vars(self) -> Dict[str, Any]:
        return self._global_state.get_all_global_vars()
    
    def get_framework_summary(self) -> Dict[str, Any]:
        return self._global_state.get_framework_summary()
        
class PluginStateAccessor:
    """插件状态访问器"""
    def __init__(self, plugin_name: str, global_state: GlobalState):
        self.plugin_name = plugin_name
        self._global_state = global_state
        self._global_state.register_plugin(plugin_name)
    
    def set_var(self, key: str, value: Any):
        """设置本插件的变量"""
        self._global_state.set_plugin_var(self.plugin_name, key, value)
    
    def get_var(self, key: str, default: Any = None) -> Any:
        """获取本插件的变量"""
        return self._global_state.get_plugin_var(self.plugin_name, key, default)
    
    def get_other_plugin_var(self, target_plugin: str, key: str, default: Any = None) -> Any:
        """获取其他插件的变量（需要授权）"""
        return self._global_state.get_other_plugin_var(self.plugin_name, target_plugin, key, default)
    
    def grant_access_to(self, authorized_plugin: str):
        """授权其他插件访问本插件的变量"""
        self._global_state.grant_access(self.plugin_name, authorized_plugin)
    
    def revoke_access_from(self, authorized_plugin: str):
        """撤销其他插件访问本插件的变量"""
        self._global_state.revoke_access(self.plugin_name, authorized_plugin)
    
    def get_all_vars(self) -> Dict[str, Any]:
        """获取本插件的所有变量"""
        return self._global_state.get_plugin_vars(self.plugin_name)
    
    def delete_var(self, key: str) -> bool:
        """删除本插件的变量"""
        return self._global_state.delete_plugin_var(self.plugin_name, key)
    
    def clear_vars(self):
        """清空本插件的所有变量"""
        self._global_state.clear_plugin_vars(self.plugin_name)

readonly_global_state = ReadOnlyGlobalState(global_state)