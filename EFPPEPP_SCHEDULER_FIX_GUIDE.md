# EFPP调度器修复补丁
# 基于EPP的修复，将相同的修复应用到EFPP

## 修复 #1: YAML解析行内注释问题

### 文件: gpfp_efpp_scheduler.cpp, 行248-269

#### 原始代码 (有BUG):
```cpp
if (line.find("use_real_solar_data:") != std::string::npos) {
    std::string value = line.substr(line.find(":") + 1);
    value.erase(0, value.find_first_not_of(" \t"));
    _use_real_solar_data = (value == "true");  // ❌ BUG: 未处理行内注释
}
```

#### 修复代码:
```cpp
if (line.find("use_real_solar_data:") != std::string::npos) {
    std::string value = line.substr(line.find(":") + 1);

    // ⭐ 修复：移除行内注释（以#开头）
    size_t comment_pos = value.find('#');
    if (comment_pos != std::string::npos) {
        value = value.substr(0, comment_pos);
    }

    value.erase(0, value.find_first_not_of(" \t"));
    value.erase(value.find_last_not_of(" \t") + 1);

    _use_real_solar_data = (value == "true");

    SCHEDULER_LOG_DEBUG(std::string("🔧 [EFPP] 解析 use_real_solar_data: ") +
                          value + " -> " +
                          (_use_real_solar_data ? "true" : "false"));
}
```

#### 其他字段的修复（solar_data_file, pv_efficiency, pv_area_m2）需要应用相同的注释处理。

---

## 修复 #2: 能量恢复时间计算错误

### 文件: gpfp_efpp_scheduler.cpp

#### 查找 calculateEnergyRecoveryTime() 函数

#### 修复内容: 从EPP的1109-1163行复制完整的实现代码

---

## 修复步骤

1. 在 gpfp_efpp_scheduler.cpp:248-269 中应用YAML修复
2. 在 gpfp_efpp_scheduler.cpp:1109-1163 中添加能量恢复时间计算函数（从EPP复制）
3. 重新编译: `cd build && make -j4`
4. 运行相同的测试用例验证修复效果
