FROM node:20-alpine

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm ci --omit=dev --ignore-scripts

# Copy source and build
COPY tsconfig.json ./
COPY src/ ./src/
RUN npm install typescript --save-dev && npx tsc

# Remove devDependencies after build
RUN npm prune --omit=dev

EXPOSE 3000

CMD ["node", "dist/index.js"]
