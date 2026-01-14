#ifndef UNIFIED_LOGGER_HPP
#define UNIFIED_LOGGER_HPP

#include <chrono>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace RTSim {
namespace Utils {

/**
 * @brief 日志级别枚举
 */
enum class LogLevel {
    DEBUG = 0,
    INFO = 1,
    WARNING = 2,
    ERROR = 3,
    CRITICAL = 4
};

/**
 * @brief 将日志级别转换为字符串
 */
inline std::string logLevelToString(LogLevel level) {
    switch (level) {
        case LogLevel::DEBUG:    return "DEBUG";
        case LogLevel::INFO:     return "INFO";
        case LogLevel::WARNING:  return "WARNING";
        case LogLevel::ERROR:    return "ERROR";
        case LogLevel::CRITICAL: return "CRITICAL";
        default:                 return "UNKNOWN";
    }
}

/**
 * @brief 将字符串转换为日志级别
 */
inline LogLevel stringToLogLevel(const std::string& levelStr) {
    if (levelStr == "DEBUG")    return LogLevel::DEBUG;
    if (levelStr == "INFO")     return LogLevel::INFO;
    if (levelStr == "WARNING")  return LogLevel::WARNING;
    if (levelStr == "ERROR")    return LogLevel::ERROR;
    if (levelStr == "CRITICAL") return LogLevel::CRITICAL;
    return LogLevel::INFO; // 默认值
}

/**
 * @brief 日志消息类
 */
class LogMessage {
public:
    LogMessage(LogLevel level, const std::string& module, 
               const std::string& message, 
               const std::string& file = "", int line = 0)
        : timestamp_(std::chrono::system_clock::now())
        , level_(level)
        , module_(module)
        , message_(message)
        , file_(file)
        , line_(line) {}
    
    std::string toString() const {
        std::ostringstream oss;
        
        // 时间戳
        auto time_t = std::chrono::system_clock::to_time_t(timestamp_);
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            timestamp_.time_since_epoch()) % 1000;
        
        oss << std::put_time(std::localtime(&time_t), "%Y-%m-%d %H:%M:%S");
        oss << "." << std::setfill('0') << std::setw(3) << ms.count();
        
        // 日志级别
        oss << " [" << std::setw(8) << std::left << logLevelToString(level_) << "]";
        
        // 模块名
        oss << " [" << std::setw(20) << std::left << module_ << "]";
        
        // 消息
        oss << " " << message_;
        
        // 文件位置（如果提供）
        if (!file_.empty() && line_ > 0) {
            oss << " (" << file_ << ":" << line_ << ")";
        }
        
        return oss.str();
    }
    
    LogLevel getLevel() const { return level_; }
    const std::string& getModule() const { return module_; }
    const std::string& getMessage() const { return message_; }
    
private:
    std::chrono::system_clock::time_point timestamp_;
    LogLevel level_;
    std::string module_;
    std::string message_;
    std::string file_;
    int line_;
};

/**
 * @brief 日志处理器接口
 */
class LogHandler {
public:
    virtual ~LogHandler() = default;
    virtual void handle(const LogMessage& message) = 0;
    virtual void flush() = 0;
};

/**
 * @brief 控制台日志处理器
 */
class ConsoleLogHandler : public LogHandler {
public:
    ConsoleLogHandler(bool useColors = true) : useColors_(useColors) {}
    
    void handle(const LogMessage& message) override {
        std::lock_guard<std::mutex> lock(mutex_);
        
        if (useColors_) {
            std::cout << getColorCode(message.getLevel());
        }
        
        std::cout << message.toString() << std::endl;
        
        if (useColors_) {
            std::cout << "\033[0m"; // 重置颜色
        }
    }
    
    void flush() override {
        std::cout.flush();
    }
    
private:
    std::string getColorCode(LogLevel level) {
        switch (level) {
            case LogLevel::DEBUG:    return "\033[36m"; // 青色
            case LogLevel::INFO:     return "\033[32m"; // 绿色
            case LogLevel::WARNING:  return "\033[33m"; // 黄色
            case LogLevel::ERROR:    return "\033[31m"; // 红色
            case LogLevel::CRITICAL: return "\033[35m"; // 紫色
            default:                 return "\033[0m";  // 默认
        }
    }
    
    std::mutex mutex_;
    bool useColors_;
};

/**
 * @brief 文件日志处理器
 */
class FileLogHandler : public LogHandler {
public:
    FileLogHandler(const std::string& filename, bool append = true) {
        file_.open(filename, append ? std::ios::app : std::ios::trunc);
        if (!file_.is_open()) {
            std::cerr << "无法打开日志文件: " << filename << std::endl;
        }
    }
    
    ~FileLogHandler() {
        if (file_.is_open()) {
            file_.close();
        }
    }
    
    void handle(const LogMessage& message) override {
        std::lock_guard<std::mutex> lock(mutex_);
        if (file_.is_open()) {
            file_ << message.toString() << std::endl;
        }
    }
    
    void flush() override {
        if (file_.is_open()) {
            file_.flush();
        }
    }
    
private:
    std::ofstream file_;
    std::mutex mutex_;
};

/**
 * @brief 统一日志管理器（单例）
 */
class UnifiedLogger {
public:
    static UnifiedLogger& getInstance() {
        static UnifiedLogger instance;
        return instance;
    }
    
    // 禁用拷贝和移动
    UnifiedLogger(const UnifiedLogger&) = delete;
    UnifiedLogger& operator=(const UnifiedLogger&) = delete;
    UnifiedLogger(UnifiedLogger&&) = delete;
    UnifiedLogger& operator=(UnifiedLogger&&) = delete;
    
    /**
     * @brief 添加日志处理器
     */
    void addHandler(std::shared_ptr<LogHandler> handler) {
        std::lock_guard<std::mutex> lock(mutex_);
        handlers_.push_back(handler);
    }
    
    /**
     * @brief 设置日志级别
     */
    void setLevel(LogLevel level) {
        std::lock_guard<std::mutex> lock(mutex_);
        level_ = level;
    }
    
    /**
     * @brief 设置模块日志级别
     */
    void setModuleLevel(const std::string& module, LogLevel level) {
        std::lock_guard<std::mutex> lock(mutex_);
        moduleLevels_[module] = level;
    }
    
    /**
     * @brief 记录日志
     */
    void log(LogLevel level, const std::string& module, 
             const std::string& message,
             const std::string& file = "", int line = 0) {
        // 检查全局日志级别
        if (level < level_) {
            return;
        }
        
        // 检查模块特定的日志级别
        auto it = moduleLevels_.find(module);
        if (it != moduleLevels_.end() && level < it->second) {
            return;
        }
        
        LogMessage logMsg(level, module, message, file, line);
        
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto& handler : handlers_) {
            handler->handle(logMsg);
        }
    }
    
    /**
     * @brief 刷新所有处理器
     */
    void flush() {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto& handler : handlers_) {
            handler->flush();
        }
    }
    
private:
    UnifiedLogger() : level_(LogLevel::INFO) {
        // 默认添加控制台处理器
        addHandler(std::make_shared<ConsoleLogHandler>());
    }
    
    ~UnifiedLogger() {
        flush();
    }
    
    std::mutex mutex_;
    std::vector<std::shared_ptr<LogHandler>> handlers_;
    std::unordered_map<std::string, LogLevel> moduleLevels_;
    LogLevel level_;
};

/**
 * @brief 模块日志记录器包装类
 */
class ModuleLogger {
public:
    ModuleLogger(const std::string& module, LogLevel level = LogLevel::INFO)
        : module_(module), level_(level) {
        UnifiedLogger::getInstance().setModuleLevel(module, level);
    }
    
    void debug(const std::string& message, 
               const std::string& file = "", int line = 0) {
        UnifiedLogger::getInstance().log(LogLevel::DEBUG, module_, 
                                         message, file, line);
    }
    
    void info(const std::string& message,
              const std::string& file = "", int line = 0) {
        UnifiedLogger::getInstance().log(LogLevel::INFO, module_,
                                         message, file, line);
    }
    
    void warning(const std::string& message,
                 const std::string& file = "", int line = 0) {
        UnifiedLogger::getInstance().log(LogLevel::WARNING, module_,
                                         message, file, line);
    }
    
    void error(const std::string& message,
               const std::string& file = "", int line = 0) {
        UnifiedLogger::getInstance().log(LogLevel::ERROR, module_,
                                         message, file, line);
    }
    
    void critical(const std::string& message,
                  const std::string& file = "", int line = 0) {
        UnifiedLogger::getInstance().log(LogLevel::CRITICAL, module_,
                                         message, file, line);
    }
    
private:
    std::string module_;
    LogLevel level_;
};

// 预定义的模块日志记录器
inline ModuleLogger& getEnergyLogger() {
    static ModuleLogger logger("energy_manager", LogLevel::INFO);
    return logger;
}

inline ModuleLogger& getSchedulerLogger() {
    static ModuleLogger logger("scheduler", LogLevel::INFO);
    return logger;
}

inline ModuleLogger& getConfigLogger() {
    static ModuleLogger logger("config", LogLevel::INFO);
    return logger;
}

inline ModuleLogger& getSimulationLogger() {
    static ModuleLogger logger("simulation", LogLevel::INFO);
    return logger;
}

inline ModuleLogger& getTraceLogger() {
    static ModuleLogger logger("trace", LogLevel::DEBUG);
    return logger;
}

// 便捷宏
#define LOG_DEBUG(module, message) \
    RTSim::Utils::UnifiedLogger::getInstance().log( \
        RTSim::Utils::LogLevel::DEBUG, module, message, __FILE__, __LINE__)

#define LOG_INFO(module, message) \
    RTSim::Utils::UnifiedLogger::getInstance().log( \
        RTSim::Utils::LogLevel::INFO, module, message, __FILE__, __LINE__)

#define LOG_WARNING(module, message) \
    RTSim::Utils::UnifiedLogger::getInstance().log( \
        RTSim::Utils::LogLevel::WARNING, module, message, __FILE__, __LINE__)

#define LOG_ERROR(module, message) \
    RTSim::Utils::UnifiedLogger::getInstance().log( \
        RTSim::Utils::LogLevel::ERROR, module, message, __FILE__, __LINE__)

#define LOG_CRITICAL(module, message) \
    RTSim::Utils::UnifiedLogger::getInstance().log( \
        RTSim::Utils::LogLevel::CRITICAL, module, message, __FILE__, __LINE__)

// 模块特定的便捷宏
#define ENERGY_LOG_DEBUG(message) \
    RTSim::Utils::getEnergyLogger().debug(message, __FILE__, __LINE__)

#define ENERGY_LOG_INFO(message) \
    RTSim::Utils::getEnergyLogger().info(message, __FILE__, __LINE__)

#define ENERGY_LOG_WARNING(message) \
    RTSim::Utils::getEnergyLogger().warning(message, __FILE__, __LINE__)

#define ENERGY_LOG_ERROR(message) \
    RTSim::Utils::getEnergyLogger().error(message, __FILE__, __LINE__)

#define SCHEDULER_LOG_DEBUG(message) \
    RTSim::Utils::getSchedulerLogger().debug(message, __FILE__, __LINE__)

#define SCHEDULER_LOG_INFO(message) \
    RTSim::Utils::getSchedulerLogger().info(message, __FILE__, __LINE__)

#define SCHEDULER_LOG_WARNING(message) \
    RTSim::Utils::getSchedulerLogger().warning(message, __FILE__, __LINE__)

#define SCHEDULER_LOG_ERROR(message) \
    RTSim::Utils::getSchedulerLogger().error(message, __FILE__, __LINE__)

} // namespace Utils
} // namespace RTSim

#endif // UNIFIED_LOGGER_HPP
