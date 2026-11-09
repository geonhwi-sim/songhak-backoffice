# Next.js 화면 이미지 (운영용). docker-compose.prod.yml이 빌드한다.
# standalone 출력: 실행에 필요한 파일만 담아 이미지가 작고, node_modules 전체가 필요 없다.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
# /api/* 를 넘길 FastAPI 주소. rewrites는 빌드할 때 정해지므로 빌드 인자로 받는다.
ARG API_URL=http://api:8000
# 데모 서버용으로 빌드할 때만 1 (로그인 창의 "데모로 둘러보기" 버튼, 데모 안내 띠)
ARG NEXT_PUBLIC_DEMO_MODE=0
ENV API_URL=$API_URL \
    NEXT_PUBLIC_DEMO_MODE=$NEXT_PUBLIC_DEMO_MODE \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
