FROM mcr.microsoft.com/playwright:v1.65.0-noble@sha256:6e3fb709361521eb27161606e6da6d2674e5e89fa16516cb37d6fdd91372273d
WORKDIR /tests
COPY package.json package-lock.json /tests/
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY playwright.config.js /tests/playwright.config.js
COPY tests /tests/tests
CMD ["npm", "test"]
