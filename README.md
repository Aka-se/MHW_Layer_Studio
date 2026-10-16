# MHW Layer Studio

无需 Blender 的《Monster Hunter: World》MOD3 分层编辑器。选择、拆分和组合服装部件，为 Mesh 分配 Group ID，再由游戏内插件按照血量控制显隐。

Standalone MOD3 layer editor for *Monster Hunter: World*. Select, split, group, and assign Group IDs to outfit meshes, then control their visibility in game according to health.

> **特别鸣谢 / Special thanks**<br>
> 感谢 [Mi1ice](https://github.com/Mi1ice) 开发并开源 [MHWI-VisualControllerExtended](https://github.com/Mi1ice/MHWI-VisualControllerExtended)。本仓库中的配套游戏插件在该项目基础上继续扩展，用于实现血量分层、随机战损和大剑命中效果。请同时尊重上游项目及第三方依赖的许可证。

推荐配合 **狩技盒子（MHW Box）** 管理、启用和停用服装 MOD。MHW Layer Studio 可以直接把狩技盒子的 `Mods_582010` 文件夹设为模组库路径。

## 下载

前往 [Releases](https://github.com/Naidong-Lin/MHW_Layer_Studio/releases/latest)：

- 安装编辑器：下载 `MHW-Layer-Studio-Setup-*.exe`；
- 启用游戏内分层：同时下载 `MHWLS-VisualController-Plugin-*.zip`。

只在游戏外编辑、预览和导出 MOD3 时，不需要 Blender 或游戏插件。

## 游戏内前置 MOD

1. 《Monster Hunter: World: Iceborne》PC 版；
2. [Stracker's Loader](https://www.nexusmods.com/monsterhunterworld/mods/1982)，用于加载 `nativePC\plugins` 中的 DLL；
3. 本项目 Release 中的 `MHWLS-VisualController-Plugin-*.zip`；
4. 使用上游变身规则时，请按 [MHWI-VisualControllerExtended 上游说明](https://github.com/Mi1ice/MHWI-VisualControllerExtended) 配置 `visual_controller_v5`。

`GreatSwordBurst` 和随机战损已经集成在配套 VisualControllerExtended 中，不需要安装单独的大剑 DLL。第三方前置 MOD 不随本仓库重新分发，请从原作者页面下载。

## 核心功能

- 打开 MOD3、模组文件夹以及 ZIP、RAR、7Z 压缩包；
- 同时查看 `helm/body/arm/wst/leg`，支持隔离、隐藏、半透明、聚焦和标准视角；
- 读取材质与贴图，显示材质、Group ID、顶点和三角面信息；
- 按独立几何拆分部件，并用空间、骨骼和 UV 辅助成组；
- 支持左右单侧组、共同父组、备注、重命名、撤销和项目保存；
- 手动设置血量层级，或将非基础部件设为随机战损；
- 在编辑器中模拟血量变化，立即观察隐藏效果；
- 导出压缩包，或备份后直接覆盖模组文件夹。

当前默认手动层级：

| Group ID | 隐藏条件 |
| --- | --- |
| 0 | 基础模型，不随血量隐藏 |
| 60 | HP ≤ 80% |
| 61 | HP ≤ 60% |
| 62 | HP ≤ 40% |
| 63 | HP ≤ 20% |

隐藏比例和层级数量可以在编辑器中自定义。

## 简单使用教程

1. 安装并打开 MHW Layer Studio。
2. 打开模组压缩包或文件夹；使用狩技盒子时，也可以先设置 `Mods_582010` 模组库路径。
3. 在模型或左侧列表选择部件。普通点击优先选择已有组，再次点击同一位置选择组内成员。
4. 如果一个 Mesh 包含多件衣物，打开“拆分并成组”，先预览建议，再应用拆分与分组。
5. 将身体、皮肤等设为 Group 0；把可隐藏衣物分配到 60、61、62、63，或开启随机战损并标记基础部件。
6. 拖动血量滑条检查效果，确认不会产生身体缺口。
7. 压缩包使用“导出”；模组文件夹可以“备份并覆盖保存”。
8. 把导出的模组交给狩技盒子启用，或安装到游戏目录。更新规则后在游戏中按 `Ctrl+F5` 重载，也可以重启游戏。

详细规则片段会导出为 `MHWLayerStudio_health_rules.ini`。手动安装时，应把规则合并到游戏实际加载的 `nativePC\plugins\MHWI-VisualControllerExtended.ini`，并先备份原文件。

## 开发日志

- **v0.6.1**：普通点击优先选择拆分组；整理编辑器与配套插件的开源发布结构。
- **v0.6.0**：加入手动分层/随机战损双模式，以及大剑命中临时战损实验功能。
- **v0.5.x**：完成材质与贴图辨识、模组库、压缩包支持、模型拆分、骨骼/UV 辅助成组、父子组和安全覆盖保存。
- **v0.1–v0.4**：完成 MOD3 读取、3D 预览、Group ID 修改、血量模拟、规则与模组导出。

完整改动可以查看 [Commits](https://github.com/Naidong-Lin/MHW_Layer_Studio/commits/main) 和 [Releases](https://github.com/Naidong-Lin/MHW_Layer_Studio/releases)。

## 仓库结构

- 仓库根目录：编辑器源码、测试和 Windows 安装脚本；
- `plugins/MHWI-VisualControllerExtended`：配套运行插件源码、默认 INI、测试和第三方许可证；
- GitHub Releases：普通用户使用的 Setup 安装包和插件 ZIP。

## 从源码运行

需要 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

构建 Windows 安装包：

```bat
build_windows.bat
```

配套插件的构建说明位于 [`plugins/MHWI-VisualControllerExtended/DEVELOPMENT.md`](plugins/MHWI-VisualControllerExtended/DEVELOPMENT.md)。

## 安全与限制

- 默认另存，不覆盖原始模组；覆盖文件夹前会创建备份并要求确认；
- 导出后会重新读取 MOD3，并检查修改范围；
- 真实贴图预览用于辨识，不还原游戏 Shader；
- 自动拆分依据几何连通关系，不能理解衣物语义，应用前请先预览；
- 游戏文件、第三方 MOD、个人配置和测试素材不会收入源码仓库。

## License

编辑器源码采用 [MIT License](LICENSE)。配套插件及其上游、第三方组件保留各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和插件目录内的许可证文件。
