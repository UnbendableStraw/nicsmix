# overlay/

Every file the mod adds to the stock root filesystem, in place. `tools/fw/build-troubadour.sh`
copies this tree into partition 2 of a stock image and additionally:

* edits `/etc/ssh/sshd_config` (root login, password + pubkey, `authorized_keys.d`), sets the root
  password (`$ROOT_PASSWORD`) and `/etc/ssh/authorized_keys.d/root` (`$SSH_PUBKEY`),
* masks `otamanager.service` (`→ /dev/null`) and enables `syng-agent.service` and
  `syng-cloud-block.service` (the `multi-user.target.wants` symlinks are created by the script,
  so this tree needs no symlinks),
* keeps `PreferredTechnologies = ethernet,wifi` and `usb` in connman's interface blacklist.

`usr/bin/syng-agent` must be the same file as `agent/syng-agent.py`.
