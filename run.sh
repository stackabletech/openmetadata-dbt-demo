#!/usr/bin/env bash
kind delete cluster
kind create cluster
stackablectl -s /home/sliebau/IdeaProjects/openmetadata-demo/openmetadata-demo/infrastructure/stack.yaml  stack install forgejo
#cat infrastructure/forgejo.yaml | yq .options | helm install forgejo oci://code.forgejo.org/forgejo-helm/forgejo -f -