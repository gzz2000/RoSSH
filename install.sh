#!/bin/bash

server=https://raw.githubusercontent.com/gzz2000/RoSSH/master
local=$HOME/.rossh

echo "Will install RoSSH to $HOME/.rossh"
read -p "Proceed? (y/n)" -n 1 -r
echo

if ! [[ $REPLY =~ ^[Yy]$ ]]; then
    exit
fi

mkdir -p $local

for filename in rossh rossh_client.py rossh_server.py rossh_common.py LICENSE; do
    echo "Downloading $filename"
    curl -s $server/$filename --output $local/$filename
done

chmod +x $local/rossh*

if [[ ":$PATH:" == *":$HOME/.local/bin:"* ]]; then
    echo "Putting rossh executable to $HOME/.local/bin."
    ln -fs $local/rossh $HOME/.local/bin
    echo "Done."
    exit
fi

if [[ ":$PATH:" == *":$HOME/bin:"* ]]; then
    echo "Putting rossh executable to $HOME/bin."
    ln -fs $local/rossh $HOME/bin
    echo "Done."
    exit
fi

read -p "Add rossh executable to ~/.local/bin? (y/n)" -n 1 -r
echo

if ! [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "You can manually add $local/rossh to your path later."
    exit
fi

mkdir -p $HOME/.local/bin
ln -fs $local/rossh $HOME/.local/bin
echo "Done. Please add the following line to your .bashrc:"
echo ""
echo "export PATH=\"\$PATH:$HOME/.local/bin\""
