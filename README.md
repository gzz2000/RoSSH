```
   ___       __________ __
  / _ \___  / __/ __/ // /
 / , _/ _ \_\ \_\ \/ _  /
/_/|_|\___/___/___/_//_/
```

# RoSSH
Robust SSH: Long live, auto reconnect ssh session that just works. No manual server installation or root privilege required. Support servers behind NAT and reverse proxies.

## Why another one?
RoSSH is more robust than most of the outstanding SSH clients. 

Perhaps the most useful feature of RoSSH is its ability to bypass NAT and firewalls without manually installing any software on the remote server.
Even with this simplicity, RoSSH supports some complex features like public key agent forwarding.

Below is a detailed feature comparison with some similar projects.

|Feature|[Mosh](https://github.com/mobile-shell/mosh)|[AutoSSH](https://github.com/samueleaton/autossh)|[EternalTerminal](https://github.com/MisterTea/EternalTerminal)|RoSSH|
| ----- | ----- | ----- | ----- | ----- |
|**No need to install remotely**|Yes|Yes|No|Yes|
|Normal user (no root required)|Yes|Yes|No|Yes|
|Long live shell|Yes|No|Yes|Yes|
|**Support servers behind NAT, firewalls, etc**|No|Yes|No|Yes|
|Support native scroll|No|Yes|Yes|Yes|
|**Port forwarding**|No|Yes|Yes|Yes|
|**Stable Agent forwarding**|No|No|Yes|Yes|
|Better experience with long delay server echo|Yes|No|No|No|


## Installation
``` shell
bash <(curl -s https://raw.githubusercontent.com/gzz2000/RoSSH/master/install.sh)
```

RoSSH supports Linux and MacOS. Windows users can install it in WSL. Please run the above one-liner in your terminal, and then follow the on-screen instructions.

This would download `rossh_server.py`, `rossh_client.py`, `rossh_common.py` and put them under `~/.rossh`.

RoSSH is written in Python. As a result, both your host and your remote server should have a Python 3.4+ interpreter -- which most UNIX distros already do.


## Usage
Just replace your familiar `ssh` commands with `rossh` instead.

``` shell
rossh <user>@<hostname>
```

You can add `-L xxxx:xxxx`, `-A`, `-X`, and other SSH client options along with the command line, or just store them in your `~/.ssh/config` and RoSSH will obey them. 

Note that you can't directly specify a command to execute at remote server, because RoSSH always opens a shell for you instead.
