#!/usr/bin/env bash
set -euo pipefail

PROJECT_KEY="esef-data-fetcher"
SONAR_URL="${SONAR_URL:-http://sonarqube.sonarqube.svc.cluster.local:9000}"
SONAR_TOKEN="${SONAR_TOKEN:-$(cat /config/.sonarqube-token 2>/dev/null || echo '')}"
SRC_DIR="src"
TEST_DIR="tests"

if [ -z "$SONAR_TOKEN" ]; then
  echo "ERROR: SONAR_TOKEN not set and /config/.sonarqube-token not found" >&2
  exit 1
fi

echo "=== [$PROJECT_KEY] Running tests with coverage ==="
python3 -m pytest "$TEST_DIR" \
  --cov="$SRC_DIR" \
  --cov-report=xml:coverage.xml \
  --junitxml=test-results.xml \
  -q

echo ""
echo "=== [$PROJECT_KEY] Running pylint ==="
python3 -m pylint $SRC_DIR $TEST_DIR \
  --output-format=parseable \
  --reports=no \
  > pylint-report.txt 2>&1 || true
echo "Pylint score: $(tail -1 pylint-report.txt)"

echo ""
echo "=== [$PROJECT_KEY] Uploading to SonarQube ==="
sonar-scanner \
  -Dsonar.projectKey="$PROJECT_KEY" \
  -Dsonar.projectName="$PROJECT_KEY" \
  -Dsonar.sources="$SRC_DIR" \
  -Dsonar.tests="$TEST_DIR" \
  -Dsonar.python.coverage.reportPaths=coverage.xml \
  -Dsonar.python.xunit.reportPath=test-results.xml \
  -Dsonar.python.pylint.reportPaths=pylint-report.txt \
  -Dsonar.host.url="$SONAR_URL" \
  -Dsonar.token="$SONAR_TOKEN" \
  -Dsonar.scm.provider=git \
  2>&1

echo ""
echo "=== Done. View results at $SONAR_URL/dashboard?id=$PROJECT_KEY ==="
