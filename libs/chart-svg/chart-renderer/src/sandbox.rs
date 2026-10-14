//! A custom chart's template renders in a child process the host spawns.
//! The instruction fuel bounds how many operations a template runs, not how
//! large a value it builds: forty `{% set a = a ~ a %}` steps cost almost no
//! fuel and ask for a terabyte, and a Rust allocation failure aborts the
//! process the engine lives in (a `dct serve` or Cloud worker). The child
//! runs the job under `alloc_budget`'s memory budget and the parent's clock,
//! so whatever the template does ends the child alone, and the parent reads
//! the result off its stdout. A fork would be cheaper but inherits every
//! mutex another thread of the host may hold (the font cache, the regex
//! pools), so the child is always a fresh process.
//!
//! The host names the child command once (`configure`): the CLI re-runs its
//! own binary, the Python extension runs the interpreter with the extension
//! loaded by path, and a test suite that wants the template semantics
//! without a process runs the job in-process.

use std::io::{Read, Write};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::sync::OnceLock;
use std::time::Duration;

use serde::de::DeserializeOwned;
use serde::Serialize;

/// What one template render may hold in memory.
pub const MEMORY_BUDGET: usize = 256 << 20;
/// How long the parent waits for the child before killing it.
pub const WALL_CLOCK: Duration = Duration::from_secs(30);

pub enum Runner {
    /// Run the job in this process: no memory or clock bound.
    InProcess,
    /// Spawn this command, which must end in `child_main`.
    Spawn(Vec<String>),
}

static RUNNER: OnceLock<Runner> = OnceLock::new();

/// Name the runner once; a second call is ignored.
pub fn configure(runner: Runner) {
    let _ = RUNNER.set(runner);
}

pub fn configured() -> bool {
    RUNNER.get().is_some()
}

/// Run `run(job)` under the configured runner.
pub fn isolated<J: Serialize + DeserializeOwned, T: Serialize + DeserializeOwned>(
    job: &J,
    run: fn(J) -> T,
    clock: Duration,
) -> Result<T, String> {
    let runner = RUNNER
        .get()
        .ok_or("custom chart sandbox: the host configured no runner")?;
    isolated_with(runner, job, run, clock)
}

pub fn isolated_with<J: Serialize + DeserializeOwned, T: Serialize + DeserializeOwned>(
    runner: &Runner,
    job: &J,
    run: fn(J) -> T,
    clock: Duration,
) -> Result<T, String> {
    match runner {
        Runner::InProcess => {
            let job: J = serde_json::from_value(serde_json::to_value(job).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
            Ok(run(job))
        }
        Runner::Spawn(command) => spawned(command, job, clock),
    }
}

/// The child's side: the job from stdin, the result as JSON on stdout.
pub fn child_main<J: DeserializeOwned, T: Serialize>(run: fn(J) -> T) {
    let mut input = Vec::new();
    std::io::stdin()
        .read_to_end(&mut input)
        .expect("custom-child: read the job from stdin");
    let job: J = serde_json::from_slice(&input).expect("custom-child: the job is JSON");
    crate::alloc_budget::arm(MEMORY_BUDGET);
    let result = run(job);
    let bytes = serde_json::to_vec(&result).expect("custom-child: the result serializes");
    let mut stdout = std::io::stdout().lock();
    stdout
        .write_all(&bytes)
        .and_then(|_| stdout.flush())
        .expect("custom-child: write the result to stdout");
}

fn spawned<J: Serialize, T: DeserializeOwned>(
    command: &[String],
    job: &J,
    clock: Duration,
) -> Result<T, String> {
    let (program, args) = command
        .split_first()
        .ok_or("custom chart sandbox: an empty child command")?;
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("custom chart sandbox: spawn {program}: {e}"))?;
    let job = serde_json::to_vec(job).map_err(|e| e.to_string())?;
    // The child reads all of stdin before it writes, so this write is never
    // blocked by an unread stdout; a child killed early closes the pipe and
    // the write fails, which the exit status below explains.
    let mut stdin = child.stdin.take().expect("piped stdin");
    let written = stdin.write_all(&job);
    drop(stdin);
    let mut stdout = child.stdout.take().expect("piped stdout");
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let read = stdout.read_to_end(&mut bytes).map(|_| bytes);
        let _ = sender.send(read);
    });
    let output = match receiver.recv_timeout(clock) {
        Ok(read) => read,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!(
                "template exceeded the engine's clock ({} seconds)",
                clock.as_secs()
            ));
        }
    };
    let status = child
        .wait()
        .map_err(|e| format!("custom chart sandbox: wait: {e}"))?;
    if status.success() {
        written.map_err(|e| format!("custom chart sandbox: hand the job over: {e}"))?;
        let bytes = output.map_err(|e| format!("custom chart sandbox: read the result: {e}"))?;
        return serde_json::from_slice(&bytes)
            .map_err(|e| format!("custom chart sandbox: the child's result: {e}"));
    }
    if aborted(&status) {
        return Err(format!(
            "template exceeded the engine's memory budget ({} MiB) or its stack",
            MEMORY_BUDGET >> 20
        ));
    }
    Err(format!("the template render ended without a result ({status})"))
}

/// A refused allocation aborts the child, as does a stack overflow; the
/// kernel's own out-of-memory kill reads the same.
#[cfg(unix)]
fn aborted(status: &std::process::ExitStatus) -> bool {
    use std::os::unix::process::ExitStatusExt;
    matches!(status.signal(), Some(libc::SIGABRT | libc::SIGKILL))
}

#[cfg(not(unix))]
fn aborted(status: &std::process::ExitStatus) -> bool {
    // Rust's abort on Windows exits with STATUS_STACK_BUFFER_OVERRUN.
    status.code() == Some(0xC000_0409_u32 as i32)
}
