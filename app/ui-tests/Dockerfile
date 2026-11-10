FROM mcr.microsoft.com/playwright:v1.49.1-noble@sha256:70e367e0cbf60340a5b5fd562f6247a34eb3196efab9f88a3dd56482d9fe09d2
WORKDIR /tests
COPY package.json package-lock.json /tests/
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY playwright.config.js /tests/playwright.config.js
COPY tests /tests/tests
CMD ["npm", "test"]
