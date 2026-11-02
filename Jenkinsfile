// LLMForge CI pipeline.
//
// Agent requirements: Linux, python3.12 (with venv or pip), Docker CLI with
// access to a Docker daemon. Plugins: Pipeline, Git, JUnit, Timestamper,
// Workspace Cleanup, SonarQube Scanner (only if RUN_SONAR is enabled).
//
// No credentials are assumed. SonarQube credentials come from the server
// configured under "Manage Jenkins > System > SonarQube servers" and are
// injected by withSonarQubeEnv, never echoed.
//
// Deployment is intentionally NOT part of this pipeline. Publishing images
// and deploying should be a separate, manually approved job that only runs
// for trusted branches (never for pull requests from forks).

pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds(abortPrevious: true)
        buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '10'))
    }

    parameters {
        string(name: 'IMAGE_TAG', defaultValue: '', description: 'Docker image tag. Empty = <version>-<short commit>.')
        string(name: 'IMAGE_NAME', defaultValue: 'llmforge', description: 'Docker image repository name.')
        booleanParam(name: 'RUN_SONAR', defaultValue: false, description: 'Run SonarQube analysis (requires a configured SonarQube server).')
        string(name: 'SONARQUBE_SERVER', defaultValue: 'SonarQube', description: 'Name of the SonarQube server configured in Jenkins.')
        booleanParam(name: 'WAIT_FOR_QUALITY_GATE', defaultValue: false, description: 'Fail the build on a failed quality gate (requires the SonarQube webhook).')
        string(name: 'TRIVY_FAIL_SEVERITY', defaultValue: 'CRITICAL', description: 'Severities that fail the build (comma-separated).')
        string(name: 'COVERAGE_MIN', defaultValue: '85', description: 'Minimum line coverage percentage.')
    }

    environment {
        VENV = "${WORKSPACE}/.ci-venv"
        REPORTS = "${WORKSPACE}/reports"
        TRIVY_IMAGE = 'aquasec/trivy:0.75.0'
        SYFT_IMAGE = 'anchore/syft:v1.54.1'
        SONAR_SCANNER_IMAGE = 'sonarsource/sonar-scanner-cli:12.3.0.4285_8.1.0'
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                script {
                    def version = sh(script: "python3.12 -c 'import app; print(app.__version__)'", returnStdout: true).trim()
                    def shortSha = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
                    env.RESOLVED_TAG = params.IMAGE_TAG?.trim() ? params.IMAGE_TAG.trim() : "${version}-${shortSha}"
                    env.IMAGE_REF = "${params.IMAGE_NAME}:${env.RESOLVED_TAG}"
                    env.VCS_REF = shortSha
                    if (!(env.RESOLVED_TAG ==~ /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/)) {
                        error("Invalid image tag: ${env.RESOLVED_TAG}")
                    }
                    currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.RESOLVED_TAG}"
                }
                sh 'mkdir -p "$REPORTS"'
            }
        }

        stage('Python environment') {
            steps {
                sh '''
                    set -eu
                    if python3.12 -m venv "$VENV" 2>/dev/null; then :; else
                        # Debian/Ubuntu without python3.12-venv: seed pip from the system pip.
                        rm -rf "$VENV"
                        python3.12 -m venv --without-pip "$VENV"
                        python3.12 -m pip --python "$VENV/bin/python" install -q pip
                    fi
                    "$VENV/bin/pip" install -q -r requirements-dev.txt
                '''
            }
        }

        stage('Lint') {
            steps {
                sh '''
                    "$VENV/bin/ruff" check --output-format=full .
                    "$VENV/bin/ruff" format --check .
                '''
            }
        }

        stage('Unit tests') {
            steps {
                // Hermetic: no Ollama, no model download (integration tests are excluded by default).
                sh '''
                    "$VENV/bin/pytest" \
                        --junitxml="$REPORTS/junit.xml" \
                        --cov --cov-report=xml:"$REPORTS/coverage.xml" --cov-report=html:"$REPORTS/htmlcov" \
                        --cov-report=term
                '''
            }
            post {
                always {
                    junit allowEmptyResults: false, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Coverage report') {
            steps {
                sh '"$VENV/bin/coverage" report --fail-under="$COVERAGE_MIN"'
            }
        }

        stage('SonarQube analysis') {
            when { expression { params.RUN_SONAR } }
            steps {
                withSonarQubeEnv(params.SONARQUBE_SERVER) {
                    // SONAR_HOST_URL / SONAR_TOKEN are injected by withSonarQubeEnv and passed by name only.
                    sh '''
                        docker run --rm \
                            -e SONAR_HOST_URL -e SONAR_TOKEN \
                            -v "$WORKSPACE:/usr/src" -w /usr/src \
                            "$SONAR_SCANNER_IMAGE" \
                            -Dsonar.projectVersion="$RESOLVED_TAG" \
                            -Dsonar.python.coverage.reportPaths=reports/coverage.xml \
                            -Dsonar.python.xunit.reportPath=reports/junit.xml
                    '''
                }
            }
        }

        stage('Quality gate') {
            when { expression { params.RUN_SONAR && params.WAIT_FOR_QUALITY_GATE } }
            steps {
                timeout(time: 10, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('Docker build') {
            steps {
                sh '''
                    docker build \
                        --build-arg VERSION="$RESOLVED_TAG" \
                        --build-arg VCS_REF="$VCS_REF" \
                        --label org.opencontainers.image.created="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
                        -t "$IMAGE_REF" .
                '''
            }
        }

        stage('Trivy scan') {
            steps {
                sh '''
                    trivy() {
                        docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
                            -v trivy-cache:/root/.cache/ -v "$REPORTS:/reports" -v "$WORKSPACE:/src:ro" \
                            "$TRIVY_IMAGE" "$@"
                    }
                    # Full report for the archive (never fails the build) ...
                    trivy image --quiet --format json --output /reports/trivy-image.json "$IMAGE_REF"
                    trivy image --quiet --format table --output /reports/trivy-image.txt "$IMAGE_REF"
                    # ... then the policy gate: fixable vulnerabilities at the configured severities fail the build.
                    trivy image --quiet --exit-code 1 --ignore-unfixed --severity "$TRIVY_FAIL_SEVERITY" "$IMAGE_REF"
                    # Misconfiguration scan of the Dockerfile (report only).
                    trivy config --quiet --skip-dirs /src/.ci-venv --format table --output /reports/trivy-config.txt /src
                '''
            }
        }

        stage('SBOM (Syft)') {
            steps {
                sh '''
                    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$REPORTS:/reports" "$SYFT_IMAGE" \
                        "docker:$IMAGE_REF" \
                        -o spdx-json=/reports/sbom.spdx.json \
                        -o cyclonedx-json=/reports/sbom.cyclonedx.json
                '''
            }
        }

        stage('Archive artifacts') {
            steps {
                sh '''
                    echo "$IMAGE_REF" > "$REPORTS/image.txt"
                    # Scanner containers write as root; hand the files back to the agent user.
                    docker run --rm --entrypoint chown -v "$REPORTS:/reports" "$TRIVY_IMAGE" -R "$(id -u):$(id -g)" /reports
                '''
                archiveArtifacts artifacts: 'reports/**', fingerprint: true, allowEmptyArchive: false
            }
        }
    }

    post {
        always {
            cleanWs(deleteDirs: true, notFailBuild: true, patterns: [[pattern: '.ci-venv/**', type: 'INCLUDE']])
        }
    }
}
