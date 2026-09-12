# syntax=docker/dockerfile:1
#
# The dashboard image carries assets only. The Helm chart supplies the nginx
# server configuration, including the /api proxy, so nothing about routing is
# baked in here.

FROM node:22-alpine AS build

WORKDIR /web
# Copy the manifests first so a source-only change reuses the dependency layer.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ ./
# Monaco is bundled rather than CDN-loaded, which makes the production build big
# enough that Node's default heap (sized from the container, and Kaniko's is not
# generous) can be exhausted mid-render. Say the size out loud instead.
ENV NODE_OPTIONS=--max-old-space-size=3072
# VITE_API_BASE is deliberately unset: the default /api/v1 is correct behind
# the chart's nginx, which proxies /api to the API service.
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine

# nginx-unprivileged already listens on 8080 as a non-root user, which is the
# port the chart expects.
COPY --from=build /web/dist /usr/share/nginx/html

EXPOSE 8080
