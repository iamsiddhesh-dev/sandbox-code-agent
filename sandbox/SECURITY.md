# Sandbox Threat Model

This document describes what the sandbox layer protects, who it protects against,
how each backend enforces that, and — just as importantly — what it does *not*
guarantee.

The operating assumption of this project is simple: **generated code is untrusted
code.** It is written by a language model that a user can influence through the
request text, so it must be treated as attacker-controlled input to the execution
layer, every single run.

## Assets

| Asset | Why an attacker wants it |
|---|---|
| Host filesystem | Source code, SSH keys, browser profiles, `.env` files on the developer's machine or the deploy host. |
| API keys (`GROQ_API_KEY`, `E2B_API_KEY`) | Directly monetizable; billed to the project owner. |
| Network egress | Exfiltration channel for anything stolen, plus a way to make this project a proxy for attacks on third parties. |
| Host compute (CPU, RAM, PIDs) | Denial of service against the developer's machine; cryptomining. |
| The E2B credit balance | Availability: a run that never terminates burns metered sandbox-minutes. |

## Adversary model

The adversary is a **malicious or injected code generator**. Concretely, three
paths lead to hostile code being executed:

1. A user sends a request whose text is crafted to steer the model into writing
   exfiltration or host-probing code (prompt injection — hardened further in
   Phase 5).
2. Untrusted data embedded in a request (a pasted CSV, a scraped page) carries
   injected instructions that the model treats as its own.
3. The model simply gets it wrong and emits destructive code with no adversary at
   all — `shutil.rmtree("/")` written in good faith is as damaging as one written
   in bad faith.

The adversary is assumed to be able to write **arbitrary Python or JavaScript**
that this system will execute. The adversary is *not* assumed to have a working
kernel or hypervisor 0-day, nor access to the host outside the sandbox.

The key design consequence: **the prompt is not a security boundary.** The prompt's
"no network, no filesystem escape" rules are a cooperative request to a model that
can be talked out of them. The sandbox is the boundary that holds when the prompt
fails.

## Boundaries by backend

Both backends implement the same `SandboxBackend` protocol, so the agent above them
is unaware of which is in use. Their isolation mechanisms are very different.

### E2B (`sandbox/e2b_backend.py`)

Isolation comes from a **Firecracker microVM** — a separate kernel, on E2B's
infrastructure, not on the host running this project. That is a strictly stronger
primitive than a container: a container escape gets an attacker to a shared host
kernel, whereas a microVM escape requires breaking the hypervisor.

| Control | What it blocks |
|---|---|
| Runs off-host entirely | Host filesystem, host processes, and host network interfaces are not merely restricted — they are not present in the guest. |
| `allow_internet_access=False` | DNS resolution inside the sandbox fails, so hostname-based egress and any library that resolves a name cannot connect. |
| `network={"deny_out": ["0.0.0.0/0"]}` | Explicit deny of all outbound CIDRs. Stated separately because `allow_internet_access=False` alone was **observed not to fail `connect()`** — see residual risks. |
| `envs={}` on sandbox create and on command exec | No host environment variable — and therefore no API key — is ever materialized inside the guest. |
| `timeout` on the command + sandbox-level lifetime | Wall-clock kill of the process, plus a hard ceiling on the sandbox itself so a hung run cannot drain metered minutes. |
| `sandbox.kill()` in a `finally` block | The sandbox is destroyed on every path out of `run()`, including exceptions. Availability/cost control, not confidentiality. |

Memory exhaustion is bounded by the microVM's own RAM allocation: an allocation
loop is killed by the guest OOM killer (observed: process terminated after ~1.5 GB,
non-zero exit) and the host is untouched by construction.

### Docker (`sandbox/docker_backend.py`)

Isolation comes from **namespaces, cgroups, and capability dropping** on the local
kernel. Weaker than a microVM — the kernel is shared with the host — so the flags
are the whole story and each one is deliberate.

| Flag | What it blocks |
|---|---|
| `--network=none` | No network interface except loopback exists in the container. Egress is impossible, not merely filtered — DNS fails immediately (`Temporary failure in name resolution`) and there is no route to any host. |
| `--read-only` | The container's root filesystem cannot be written, so no persistence, no tampering with interpreters or libraries between the write and the exec. |
| `--cap-drop=ALL` | Removes every Linux capability, including `CAP_SYS_ADMIN`, `CAP_NET_RAW`, `CAP_DAC_OVERRIDE`, and `CAP_MKNOD`. Kills the classic mount-based and device-node-based escape paths. |
| `--security-opt=no-new-privileges` | A setuid binary inside the image cannot raise privileges, so capability dropping cannot be undone from within. |
| `--user=runner` (non-root, uid 1000, set in the image) | Nothing runs as uid 0, so even a namespace weakness is being exercised from an unprivileged account. |
| `--pids-limit=64` | Fork bombs terminate against the cgroup PID limit instead of exhausting host process slots. |
| `--memory=512m` + `--memory-swap=512m` | The memory cgroup OOM-kills the process (observed: exit 137 after ~500 MB) rather than pushing the host into swap or triggering the host OOM killer. Equal values disable swap, so the limit cannot be evaded by paging out. |
| `--cpus=1` | Caps CPU shares so a spin loop degrades one core's worth of throughput, not the whole machine. |
| `--tmpfs /tmp`, `--tmpfs /output` (`noexec,nosuid,nodev`, 64 MB each) | The only writable paths are RAM-backed, size-capped, and destroyed with the container. `noexec` prevents dropping and running a downloaded binary; `nosuid`/`nodev` prevent setuid and device-node tricks. |
| No host bind mounts | There is no path by which any host file is visible inside the container. |
| `timeout -k 2 {timeout_s}` in-container + `subprocess` timeout on the host | Two independent wall-clock kills. The in-container one produces exit 124 and still allows artifact extraction; the host-side one is the backstop for a container that stops responding entirely, followed by `docker rm -f`. |
| Fresh container per run (`--rm`) | No state carries between runs, so one request cannot plant something for the next. |

**Artifact extraction and output framing.** Because `/output` is tmpfs, it ceases to
exist when the container stops, so files are streamed out *before* exit: the runner
script tars `/output`, base64-encodes it, and emits it on stdout after the program
finishes. Program stdout and this payload therefore share one stream, which the
executed code could otherwise forge. The framing markers embed a **per-run random
nonce** the code cannot predict, so a program cannot fake its own exit code or inject
artifacts into `ExecResult.files`.

## The no-secrets rule

**No secret is ever mounted into the sandbox environment.** Not scrubbed after the
fact, not redacted on output — never placed there in the first place.

- E2B: `envs={}` is passed both at sandbox creation and at command execution.
- Docker: `docker run` is invoked with no `-e`/`--env-file`, so the container gets
  only the image's own environment.

This is what makes prompt injection survivable rather than catastrophic: an
adversary who wins *complete* control of the generated code still finds an
environment with nothing worth stealing and no route out. Enforced by
`tests/test_sandbox_security.py::test_no_secrets_in_sandbox_env` on both backends.

## Verified boundaries

`tests/test_sandbox_security.py` runs against **both** backends (`pytest -m slow`):

| Test | Assertion |
|---|---|
| `test_no_network` | An HTTP fetch never succeeds; the run ends non-zero or timed out. |
| `test_no_raw_socket_response` | Even when `connect()` reports success, zero bytes ever come back over the socket. |
| `test_no_filesystem_escape` | `/etc/shadow`, `../../../../` traversal, `/var/run/docker.sock`, and Windows host paths all read as unavailable; no host file content reaches stdout. |
| `test_no_secrets_in_sandbox_env` | No host environment variable — API keys or identity — is visible inside the sandbox. |
| `test_memory_bomb_is_killed` | A 100 MB-per-iteration allocation loop is killed; the host test process survives to make the assertion. |
| `test_infinite_loop_is_timed_out` | `while True: pass` yields `timed_out=True` within the deadline plus provisioning overhead. |

## Residual risks

Honesty about what is *not* covered matters more than the list of what is.

1. **E2B `connect()` succeeds even with internet access disabled.** Measured
   behavior: DNS fails, and a raw TCP `connect()` to a literal IP *returns success*,
   but no response data ever arrives (`recv()` returns 0 bytes). This is consistent
   with a blackhole that accepts the SYN and discards traffic. From inside the guest
   it is not possible to prove that outbound bytes never leave E2B's edge, so a
   **blind, one-way exfiltration channel cannot be fully ruled out** on this backend.
   The Docker backend has no such ambiguity — with `--network=none` there is no
   interface at all. For adversarial work where egress must be provably impossible,
   prefer the Docker backend.
2. **Container escape 0-days.** The Docker backend shares the host kernel. A kernel
   or runc vulnerability defeats every flag in the table above simultaneously. The
   mitigations are keeping Docker patched and preferring E2B where the trust
   requirement is high.
3. **Docker daemon trust.** The daemon runs as root on the host. This project is
   trusted to construct the `docker run` argument list correctly; a bug that dropped
   a hardening flag would silently weaken the boundary with no visible symptom. The
   flags are therefore defined once as a module-level constant rather than assembled
   per call site.
4. **Availability and cost, not confidentiality.** E2B free tier is metered. A
   pathological run cannot escape, but it can burn credits. Sandbox lifetime caps
   and `finally`-block teardown bound this; the per-request budget ceiling is
   Phase 3's job.
5. **Side channels are out of scope.** Timing, cache, and speculative-execution
   attacks against co-tenants on E2B infrastructure are not addressed here and are
   not a realistic threat for this workload.
6. **The prompt layer is not counted as a defense.** Any security claim in this
   document rests on the sandbox alone. Phase 5 adds prompt-level hardening as an
   *additional* layer, not as a substitute for anything above.
