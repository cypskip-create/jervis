FROM node:22-alpine AS build
WORKDIR /dashboard
COPY package*.json ./
RUN npm ci
COPY . ./
ARG VITE_API_BASE=/api/v1
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /dashboard/dist /usr/share/nginx/html
EXPOSE 8080
