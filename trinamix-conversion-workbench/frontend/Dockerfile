# ----- build stage -----
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . ./
RUN npm run build

# ----- runtime stage (nginx) -----
FROM nginx:1.27-alpine

# Custom nginx config that proxies /api → backend container
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy SPA build
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
