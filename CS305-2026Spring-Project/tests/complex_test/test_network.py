"""
Complex topology demo for shortest-path switching and firewall.

Ring: s1-s2-s3-s4-s5-s6-s7-s1
Hosts: h_i connects to s_i only (i = 1..7)

Usage:
  sudo env "PATH=$PATH" python test_network.py
  sudo env "PATH=$PATH" python test_network.py --firewall
"""

import argparse
import time

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo


def disable_ipv6(node):
    node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")


def send_arp(node, count=1):
    node.cmd('arping -c %s -A -I %s-eth0 %s' % (count, node.name, node.IP()))


def do_arp_all(net):
    for h in net.hosts:
        send_arp(h)


class ComplexTopo(Topo):
    """7 switches in a ring, 7 hosts (h_i -- s_i)."""

    def __init__(self, firewall_mode=False, **opts):
        Topo.__init__(self, **opts)
        self.firewall_mode = firewall_mode

        switches = [self.addSwitch('s%d' % i) for i in range(1, 8)]

        if firewall_mode:
            host_ips = ['10.10.0.%d/24' % i for i in range(1, 8)]
        else:
            host_ips = [None] * 7

        hosts = []
        for i in range(1, 8):
            ip = host_ips[i - 1]
            if ip:
                hosts.append(self.addHost('h%d' % i, ip=ip))
            else:
                hosts.append(self.addHost('h%d' % i))

        for i in range(7):
            self.addLink(hosts[i], switches[i])

        for i in range(7):
            self.addLink(switches[i], switches[(i + 1) % 7])


def run_mininet(firewall_mode=False):
    topo = ComplexTopo(firewall_mode=firewall_mode)
    net = Mininet(topo=topo, autoSetMacs=True, controller=RemoteController)

    for h in net.hosts:
        disable_ipv6(h)
    for s in net.switches:
        disable_ipv6(s)

    net.start()
    time.sleep(5)

    for _ in range(3):
        do_arp_all(net)
        time.sleep(2)

    print('\n===== Complex topology ready (7-switch ring) =====')
    if firewall_mode:
        print('Firewall mode: see TEST_RUNBOOK.md §5.2')
        h1 = net.get('h1')
        print(h1.cmd('ping -c 2 -W 2 10.10.0.5'))
        print(h1.cmd('ping -c 2 -W 2 10.10.0.3'))
    else:
        print('Switching demo: see TEST_RUNBOOK.md §5.1')
        print('After switching: cp firewall_rule_complex.json to firewall_rules.json,')
        print('  restart controller, re-run with --firewall (§5.2)')
    CLI(net)

    net.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Complex ring topology Mininet demo')
    parser.add_argument(
        '--firewall',
        action='store_true',
        help='Use fixed 10.10.0.x IPs for firewall complex demo',
    )
    args = parser.parse_args()
    setLogLevel('info')
    run_mininet(firewall_mode=args.firewall)
