#!/usr/bin/env python3
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI
from mininet.log import setLogLevel

class FwCrossSwitchTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        h1 = self.addHost('h1', ip='192.168.1.2/24')
        h2 = self.addHost('h2', ip='192.168.1.3/24')
        h3 = self.addHost('h3', ip='192.168.1.4/24')
        h4 = self.addHost('h4', ip='192.168.1.5/24')
        self.addLink(h1,s1)
        self.addLink(h2,s1)
        self.addLink(h3,s2)
        self.addLink(h4,s2)
        self.addLink(s1,s2)

if __name__ == '__main__':
    setLogLevel('info')
    topo=FwCrossSwitchTopo()
    net=Mininet(topo=topo,controller=RemoteController('c0','127.0.0.1',6653))
    net.start()
    h1,h2,h3,h4 = net.hosts
    h3.cmd('python3 -m http.server 80 &')
    h3.cmd('python3 -m http.server 9999 &')
    h2.cmd('python3 -m http.server 80 &')
    print("====跨交换机防火墙复杂测试====")
    print("测试1：h1 ping h3(192.168.1.4) → ICMP被防火墙拦截，全丢包")
    print("测试2：h1 curl h2:80 → TCP:80被拦截，超时")
    print("测试3：h4 ping h3 → ICMP无规则，正常通")
    print("测试4：h1 curl h3:9999 → UDP9999被拦截")
    CLI(net)
    net.stop()