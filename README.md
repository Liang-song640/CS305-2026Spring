# CS305-2026Spring

CS305 计算机网络 · 2026 春季学期课程项目仓库，包含 **SDN 控制器项目代码** 与 **项目报告**。

## 目录结构

| 路径 | 说明 |
|---|---|
| [`CS305-2026Spring-Project/`](CS305-2026Spring-Project) | 项目代码：基于 os-ken + Mininet 的 SDN 控制器 |
| [`report.pdf`](report.pdf) | 项目报告（架构说明、实现细节与测试） |

## 项目内容

基于 os-ken 框架编写的集中式 SDN 控制器，在 Mininet 模拟网络中实现三大功能：

1. **DHCP 服务器** —— 识别 DHCP 报文，为主机自动分配 IP 地址
2. **最短路径转发** —— 基于全局拓扑信息计算任意两台主机间的最短路径，并下发流表规则
3. **防火墙** —— 解析防火墙规则（`firewall_rules.json`），以高优先级 OpenFlow 流表项实现报文拦截

## 环境要求

- Linux（推荐 Mininet 官方 Ubuntu 虚拟机）
- Python 3.8（Miniconda 环境）
- os-ken、Mininet

## 运行方法

```bash
# 终端 1：启动控制器
osken-manager --observe-links controller.py

# 终端 2：运行测试（以 DHCP 测试为例）
cd tests/dhcp_test/
sudo env "PATH=$PATH" python test_network.py
```

详细的搭建步骤、实现说明与测试方法见 [`CS305-2026Spring-Project/README.md`](CS305-2026Spring-Project/README.md) 和 [`TEST_RUNBOOK.md`](CS305-2026Spring-Project/TEST_RUNBOOK.md)。

## 提交信息

- 报告：`report.pdf`（需包含架构说明、实现细节与复杂测试用例）
- 源码：`src.zip`（提交前自行打包）

## 声明

本仓库为课程作业的公开存档，仅供学习交流参考。请勿直接复制代码提交为作业，学术诚信责任自负。
