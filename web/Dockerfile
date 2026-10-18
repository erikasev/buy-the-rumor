# Web service image for Elastic Beanstalk (Docker platform).
# Build stage: compile a static binary with no C dependencies.
FROM golang:1.24 AS build
WORKDIR /src
COPY go.mod ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/web .

# Run stage: distroless has no shell or package manager, so a compromised
# process has very little to work with. Runs as a non-root user.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/web /web
ENV PORT=8080
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/web"]
