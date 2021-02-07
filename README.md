# RoSSH
Robust SSH: Long live, auto reconnect ssh session that just works. No manual server installation or root privilege required. Support servers behind NAT and reverse proxies.

## Usage
Just replace `ssh` with `rossh`.
``` shell
rossh <hostname>
```

## Install
RoSSH supports Linux and MacOS. Windows users can install it in WSL. Please run the following in your terminal, then follow the on-screen instructions.
``` shell
bash <(curl -s https://raw.githubusercontent.com/gzz2000/RoSSH/master/install.sh)
```

This would download `rossh_server.py`, `rossh_client.py`, `rossh_common.py` and put them under `~/.rossh`.

RoSSH is implemented using python. So both your host and your remote server should have a python 3.4+ interpreter (which most UNIX distros already do).


