FROM node:24-alpine

WORKDIR /app
RUN chown node:node /app

COPY --chown=node:node package.json package-lock.json ./
USER node
RUN npm ci --ignore-scripts

COPY --chown=node:node . .
RUN npm run typecheck && npm test && npm run build
