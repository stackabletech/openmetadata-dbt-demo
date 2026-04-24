# syntax=docker/dockerfile:1.6

FROM rust:1.88-bookworm AS builder
WORKDIR /src

# Cache dep compilation: copy manifests first, build a stub, then copy sources.
COPY Cargo.toml Cargo.lock* ./
RUN mkdir -p src && echo 'fn main() {}' > src/main.rs && \
    cargo build --release && rm -rf src

COPY src ./src
COPY assets ./assets
RUN touch src/main.rs && cargo build --release

FROM gcr.io/distroless/cc-debian13:nonroot
WORKDIR /
COPY --from=builder /src/target/release/demo-landing /usr/local/bin/demo-landing
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/demo-landing"]
