#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一日志系统 - Python版本

提供统一的日志接口，支持：
1. 多级别日志（DEBUG, INFO, WARNING, ERROR, CRITICAL）
2. 统一的日志格式
3. 模块化日志记录
4. 文件和控制台输出
5. 性能优化（避免频繁的字符串格式化）
"""

import os
import sys
import logging
import logging.handlers
from datetime import datetime
from typing import Optional, Dict, Any, Union
from enum import IntEnum


class LogLevel(IntEnum):
    """日志级别枚举"""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class UnifiedLogger:
    """统一日志管理器"""
    
    # 单例实例
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(UnifiedLogger, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._loggers: Dict[str, logging.Logger] = {}
            self._default_level = LogLevel.INFO
            self._log_dir = "logs"
            self._setup_log_directory()
            self._initialized = True
    
    def _setup_log_directory(self):
        """设置日志目录"""
        if not os.path.exists(self._log_dir):
            os.makedirs(self._log_dir, exist_ok=True)
    
    def get_logger(self, module_name: str, 
                   level: Optional[LogLevel] = None,
                   log_to_file: bool = True,
                   log_to_console: bool = True) -> logging.Logger:
        """
        获取指定模块的日志记录器
        
        Args:
            module_name: 模块名称
            level: 日志级别，如果为None则使用默认级别
            log_to_file: 是否记录到文件
            log_to_console: 是否输出到控制台
            
        Returns:
            logging.Logger: 配置好的日志记录器
        """
        if module_name in self._loggers:
            return self._loggers[module_name]
        
        # 创建新的日志记录器
        logger = logging.getLogger(module_name)
        
        # 设置日志级别
        log_level = level if level is not None else self._default_level
        logger.setLevel(log_level.value)
        
        # 避免重复添加处理器
        if logger.handlers:
            self._loggers[module_name] = logger
            return logger
        
        # 创建格式化器
        formatter = logging.Formatter(
            fmt='%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)-20s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 控制台处理器
        if log_to_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(log_level.value)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        
        # 文件处理器
        if log_to_file:
            # 按日期滚动的文件处理器
            log_file = os.path.join(self._log_dir, f"{module_name}.log")
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=log_file,
                when='midnight',  # 每天午夜滚动
                interval=1,
                backupCount=7,    # 保留7天日志
                encoding='utf-8'
            )
            file_handler.setLevel(log_level.value)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        
        # 避免日志传播到根记录器
        logger.propagate = False
        
        self._loggers[module_name] = logger
        return logger
    
    def set_default_level(self, level: LogLevel):
        """设置默认日志级别"""
        self._default_level = level
        for logger in self._loggers.values():
            logger.setLevel(level.value)
            for handler in logger.handlers:
                handler.setLevel(level.value)
    
    def set_log_directory(self, log_dir: str):
        """设置日志目录"""
        self._log_dir = log_dir
        self._setup_log_directory()
    
    def shutdown(self):
        """关闭所有日志处理器"""
        for logger in self._loggers.values():
            for handler in logger.handlers:
                handler.close()
        self._loggers.clear()


# 全局日志管理器实例
_logger_manager = UnifiedLogger()


# 便捷函数
def get_logger(module_name: str, 
               level: Optional[LogLevel] = None,
               log_to_file: bool = True,
               log_to_console: bool = True) -> logging.Logger:
    """
    获取指定模块的日志记录器（便捷函数）
    """
    return _logger_manager.get_logger(module_name, level, log_to_file, log_to_console)


def set_default_level(level: LogLevel):
    """设置默认日志级别（便捷函数）"""
    _logger_manager.set_default_level(level)


def set_log_directory(log_dir: str):
    """设置日志目录（便捷函数）"""
    _logger_manager.set_log_directory(log_dir)


def shutdown_logging():
    """关闭日志系统（便捷函数）"""
    _logger_manager.shutdown()


# 性能优化的日志函数（避免不必要的字符串格式化）
class LazyMessage:
    """惰性求值的日志消息，避免不必要的字符串格式化"""
    
    def __init__(self, func, *args, **kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
    
    def __str__(self):
        return self.func(*self.args, **self.kwargs)


def lazy_message(func):
    """装饰器：创建惰性求值的日志消息"""
    def wrapper(*args, **kwargs):
        return LazyMessage(func, *args, **kwargs)
    return wrapper


# 模块特定的日志记录器
class ModuleLogger:
    """模块专用的日志记录器包装类"""
    
    def __init__(self, module_name: str, 
                 level: Optional[LogLevel] = None,
                 log_to_file: bool = True,
                 log_to_console: bool = True):
        self.module_name = module_name
        self.logger = get_logger(module_name, level, log_to_file, log_to_console)
        # 添加 name 属性以兼容标准 logging.Logger 接口
        self.name = module_name
    
    def debug(self, msg: Union[str, LazyMessage], *args, **kwargs):
        """记录DEBUG级别日志"""
        self.logger.debug(msg, *args, **kwargs)
    
    def info(self, msg: Union[str, LazyMessage], *args, **kwargs):
        """记录INFO级别日志"""
        self.logger.info(msg, *args, **kwargs)
    
    def warning(self, msg: Union[str, LazyMessage], *args, **kwargs):
        """记录WARNING级别日志"""
        self.logger.warning(msg, *args, **kwargs)
    
    def error(self, msg: Union[str, LazyMessage], *args, **kwargs):
        """记录ERROR级别日志"""
        self.logger.error(msg, *args, **kwargs)
    
    def critical(self, msg: Union[str, LazyMessage], *args, **kwargs):
        """记录CRITICAL级别日志"""
        self.logger.critical(msg, *args, **kwargs)
    
    def exception(self, msg: Union[str, LazyMessage], *args, exc_info=True, **kwargs):
        """记录异常信息"""
        self.logger.exception(msg, *args, exc_info=exc_info, **kwargs)
    
    def setLevel(self, level: Union[LogLevel, int]):
        """设置日志级别"""
        if isinstance(level, LogLevel):
            level_value = level.value
        else:
            level_value = level
        self.logger.setLevel(level_value)
        # 同时更新所有处理器的级别
        for handler in self.logger.handlers:
            handler.setLevel(level_value)
    
    def getEffectiveLevel(self) -> int:
        """获取有效日志级别"""
        return self.logger.getEffectiveLevel()
    
    def isEnabledFor(self, level: Union[LogLevel, int]) -> bool:
        """检查是否启用指定级别的日志"""
        if isinstance(level, LogLevel):
            level_value = level.value
        else:
            level_value = level
        return self.logger.isEnabledFor(level_value)


# 预定义的模块日志记录器
def get_energy_logger() -> ModuleLogger:
    """获取能量管理模块的日志记录器"""
    return ModuleLogger("energy_manager", LogLevel.INFO)


def get_scheduler_logger() -> ModuleLogger:
    """获取调度器模块的日志记录器"""
    return ModuleLogger("scheduler", LogLevel.INFO)


def get_config_logger() -> ModuleLogger:
    """获取配置模块的日志记录器"""
    return ModuleLogger("config", LogLevel.INFO)


def get_simulation_logger() -> ModuleLogger:
    """获取仿真模块的日志记录器"""
    return ModuleLogger("simulation", LogLevel.INFO)


def get_trace_logger() -> ModuleLogger:
    """获取追踪模块的日志记录器"""
    return ModuleLogger("trace", LogLevel.DEBUG)


# 测试函数
def test_unified_logger():
    """测试统一日志系统"""
    print("=== 测试统一日志系统 ===")
    
    # 获取不同模块的日志记录器
    energy_logger = get_energy_logger()
    scheduler_logger = get_scheduler_logger()
    
    # 测试不同级别的日志
    energy_logger.debug("这是一条DEBUG消息（通常不会显示）")
    energy_logger.info("能量管理器初始化完成")
    energy_logger.warning("能量水平较低：50J")
    energy_logger.error("能量收集失败")
    
    scheduler_logger.info("调度器开始运行")
    scheduler_logger.info("调度任务：task_1")
    
    # 测试惰性求值消息
    @lazy_message
    def complex_message(task_count, energy_used):
        return f"处理了{task_count}个任务，消耗{energy_used:.2f}J能量"
    
    energy_logger.info(complex_message(10, 25.5))
    
    print("=== 测试完成 ===")


if __name__ == "__main__":
    # 设置日志级别为DEBUG以显示所有消息
    set_default_level(LogLevel.DEBUG)
    test_unified_logger()
    shutdown_logging()
