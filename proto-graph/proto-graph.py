#!/usr/bin/env python

import argparse
import os
from dataclasses import dataclass, asdict
from typing import List, Set, Iterable, Optional
import sys
from itertools import chain
from yaml import load, dump
from pathlib import Path

try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper
import networkx as nx
from pyvis.network import Network
import json


@dataclass
class Service:
    id: str
    namespace: str
    producers: List[str]
    consumers: List[str]


def read_service(deployment_yaml_file: str) -> Optional[Service]:

    print(f"read_service: reading {deployment_yaml_file}")

    try:
        with open(deployment_yaml_file) as stream:
            data = load(stream, Loader=Loader)
            id_ = data["metadata"]["name"]
            namespace = data["metadata"]["namespace"]
            kafka = data["spec"]["template"]["spec"]["deployment"]["kafka"]
            producers = {topic["topicName"] for topic in kafka.get("producers", [])}
            try:
                consumers = {
                    topic["topicName"]
                    for topic in chain.from_iterable(
                        consumer_group["topics"]
                        for consumer_group in kafka.get("consumers", [])
                    )
                }
            except:
                consumers = {topic["topicName"] for topic in kafka.get("consumers", [])}
            return Service(
                id=id_, namespace=namespace, producers=list(producers), consumers=list(consumers)
            )
    except Exception as e:
        print(e)
        return None


def build_proto_map(services: Iterable[Service]):
    proto_mapping = {}
    for service in services:
        protos = service.producers
        for proto in protos:
            proto_mapping.setdefault(proto, [])
            proto_mapping[proto].append(service.id)
    return proto_mapping


def deployment_files(base_dir: str):
    is_deployment = lambda d: d == "deployment.yaml" or d == "deployment.yml"
    for root, dirs, files in os.walk(base_dir):
        for file in files: 
            if is_deployment(file):
                yield Path(root) / "deployment.yaml"


def build_graph(services: Iterable[Service]):
    proto_map = build_proto_map(services)
    net = Network("100%", "100%", notebook=True, directed=True)
    for service in services:
        title = json.dumps(asdict(service), indent=4)
        net.add_node(service.id, group=service.namespace, title=f"<h3>{service.id}</h3><pre>{title}</pre>")
    for service in services:
        for proto in service.consumers:
            if proto in proto_map:
                for dep in proto_map[proto]:
                    if dep != service.id:
                        net.add_edge(dep, service.id, title=proto)
    return net


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate a protobuf dependency graph')
    parser.add_argument("-o", "--output", default="out.html", help="Output file name")
    parser.add_argument("base", help="Base directory to scan for deployment.yaml files")
    args = parser.parse_args()

    services = [read_service(deployment_yaml) for deployment_yaml in deployment_files(args.base)]
    services = [s for s in services if s is not None]

    filename = args.output
    net = build_graph(services)
    options = """{
      "edges": {
        "color": {
          "inherit": true
        },
        "dashes": true,
        "smooth": true
      },
      "physics": {
        "barnesHut": {
          "gravitationalConstant": -30000,
          "springConstant": 0,
          "damping": 0.94,
          "avoidOverlap": 1,
          "centralGravity": 3.05
        },
        "minVelocity": 0.75
      }
    }
    """
    #net.set_edge_smooth('dynamic')
    net.set_options(options)
    # net.show_buttons()
    net.show(filename)

    summary = {
        service.id: {
            "producers": service.producers,
            "consumers": service.consumers,
        }
        for service in services
    }
    print(json.dumps(summary))
