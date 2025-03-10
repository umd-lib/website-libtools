# website-libtools 

Python 3 FastAPI service to interact with LibApps.

## Requires

* Python 3.13.2

### Development Setup

See [docs/DevelopmentSetup.md](docs/DevelopmentSetup.md).

### Running in Docker

```bash
$ docker build -t docker.lib.umd.edu/website-libtools .
$ docker run -it --rm -p 5000:5000 --env-file=.env --read-only docker.lib.umd.edu/website-libtools
```

### Building for Kubernetes

```bash
$ docker buildx build . --builder=kube -t docker.lib.umd.edu/website-libtools:VERSION --push
```

### Documentation

Available endpoints can be found at /api/libtools/docs
