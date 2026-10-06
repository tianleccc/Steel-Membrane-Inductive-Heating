# Steel Membrane · 成像与红外加热控制台

树莓派 5 本地 Web 面板：Camera Module 3 实时预览、荧光激发光、定时拍照、历史照片浏览，以及 MLX90614 红外温度 PID 控制。浏览器打开 `http://<树莓派 IP>:8080`。

## 硬件与接线

| 功能 | 配置 |
|---|---|
| 加热 MOSFET | BCM GPIO 24，物理引脚 18，高电平开启 |
| 激发光 MOSFET | BCM GPIO 16，物理引脚 36，默认高电平开启 |
| MLX90614 | I²C 1，默认地址 0x5A；SDA=GPIO 2，SCL=GPIO 3 |
| 相机 | Camera Module 3；本机识别为 imx708_wide |

以传感器模块额定电压接电，确保 I²C 上拉电压与 Pi 3.3 V 逻辑兼容；所有控制共地。GPIO 只驱动 MOSFET/驱动接口，不直接供给加热负载。MOSFET 栅极应有硬件下拉，保证断电、进程崩溃和重启时不会自行开启。

原始温控程序是 `temp.py`（MLX90614），不是 `heating.py`（MAX31855）。代码默认 GPIO 24 与旧注释 GPIO 20 不一致，已按用户确认使用 GPIO 24。

## 新树莓派部署

使用 64 位 Raspberry Pi OS，连接网络、相机及传感器，启用 SSH。当前设备已验证运行 Debian 13 / Raspberry Pi OS Trixie。

```bash
git clone https://github.com/tianleccc/Steel-Membrane-Inductive-Heating.git ~/steel-membrane
cd ~/steel-membrane
bash scripts/install.sh
```

安装脚本启用 I²C、安装系统相机/GPIO 依赖，创建带系统包访问的虚拟环境，并注册 systemd 服务。请使用普通用户运行脚本；sudo 提示时输入该设备密码。

检查 `config.json` 的引脚及高低电平配置。首次复制时 `heater_enabled` 默认是 `false`；接线正确后改为 `true`。传感器未连接/无有效读数时，软件仍拒绝加热。连接传感器或修改配置后：

```bash
sudo systemctl restart steel-membrane
```

每次启动均保持加热、激发光和拍照任务关闭，不恢复先前实验。Web 服务开机自动启动。

## 面板使用

- **实时观察**：约每秒更新 3–4 次预览；只看画面时默认不点亮激发光。
- **荧光激发光**：手动打开持续照明以观察荧光。页面隐藏时主动关闭；失去续期后 15 秒内关闭。长时间照射可能引起漂白。
- **拍一张 / 定时采集**：自动点亮 GPIO 16，默认预热 1 秒，保存 4608×2592 JPEG，拍摄后关闭光源（如未开启持续照明）。保留原脚本手动焦点 11.5、自动曝光及自动白平衡设置。
- **定时采集**：第一张立即开始，之后按拍摄开始时间计算间隔；处理耗时超过间隔则跳过错过的时间点，不追赶连拍。拍照间隔 2–86400 秒，数量 1–100000 张。
- **红外温控**：设定目标温度、从点击开始计时的持续时长、占空比上限。原脚本的 PID 默认值为 12 / 0.05 / 0.1；每 5 个 0.1 秒周期停止输出采样，三次读数取中位数并做 EMA。输出上限表示占空比，并非实测功率。
- **照片档案**：日期筛选按 UTC，显示时间按浏览器本地时间；分页浏览、大图左右翻阅、下载原图。时间戳文件名使用 UTC。
- **温度日志**：每次加热单独创建 CSV，每次采样立即写入并 flush，不等退出才保存。
- **全部停止**：停止加热、当前/后续拍照和激发光。已经完成的照片保留。

加热和定时采集相互独立。关闭浏览器不会停止已启动的限时加热或定时拍照；如需停止请先按“全部停止”。加热最长默认 2 小时；达到时间会关闭输出。

## 温度处理与保护

传感器故障、无效值、超时读数和原始采样达到 110 °C 会锁定停止加热。恢复正常温度后需手动清除故障，再重新开始。检测过温先检查每个原始读数，不被中位数/EMA 延迟。占空比上限最后应用，最低占空比不会覆盖上限。

旧 `temp.py` 的软件发射率公式在 ε≠1 时存在问题；本版**不使用这个修正**，直接记录 MLX90614 自身输出，也不改写传感器 EEPROM。默认行为与旧代码 ε=1 一致。金属表面红外读数依赖发射率、反射环境及视场，实际恒温效果需在传感器安装后进行测温对照和 PID 调整。原脚本基于电阻估算的电流/功率限制未暴露在面板中，因为系统没有实际电流/功率反馈。

软件保护依赖传感器、操作系统及程序正常运行，不等价于硬件急停/独立过温断电。生产运行应保留独立硬件保护。

## 数据与 Git 维护

`data/photos/` 保存原图、缩略图和逐张 JSON 元数据；`data/logs/` 保存 CSV。`config.json` 是当前设备的本地设置。它们都被 Git 忽略，不随代码推送。树莓派 SSH 密码、访问令牌和密钥不进入仓库。

推荐在电脑上编辑并提交到 GitHub，在树莓派拉取已审阅版本：

```bash
# 电脑 / 开发环境
git add <修改的代码文件>
git commit -m "Describe the change"
git push origin main

# 树莓派：会停止当前实验，更新并重新启动到关闭输出的待机状态
cd ~/steel-membrane
bash scripts/update.sh
```

更新脚本拒绝覆盖未提交修改或本地未推送提交，使用 fast-forward 更新。公开仓库拉取不需要在 Pi 保存 GitHub 密码；如要从 Pi 直接推送，应为该设备单独配置 GitHub SSH key / deploy key，不要复制其他设备私钥。

备份实验数据时单独备份 `data/` 与 `config.json`。复制新系统使用上述安装脚本；不要克隆旧设备 SSH 主机密钥、网络配置或登录凭据。

## 服务维护与诊断

```bash
sudo systemctl status steel-membrane
journalctl -u steel-membrane -n 100 --no-pager
sudo systemctl stop steel-membrane
sudo systemctl start steel-membrane
cd ~/steel-membrane
.venv/bin/python -m unittest discover -s tests -v
```

MLX90614 未连接会显示初始化失败，拍照功能仍可独立使用。断电后正确连接传感器，再启动/重启服务。不要同时运行旧 `camera.py` / `temp.py`，否则会争用相机或 GPIO。面板使用单进程持有设备锁，不能以多个 WSGI worker 启动。

服务面向可信局域网，无互联网远程登录功能；同一局域网中能访问面板的人可以控制实验。跨站控制请求需同源页面令牌。不要直接把 8080 端口映射到公网；远程维护可使用 SSH 隧道。

## 上游文档

- [Raspberry Pi Picamera2 官方手册](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [Melexis MLX90614 数据手册](https://www.melexis.com/-/media/files/documents/datasheets/mlx90614-datasheet-melexis.pdf)
