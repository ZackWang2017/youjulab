import { spawn, execFile, spawnSync } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Only processes launched by this app are eligible for cleanup. */
export class ServiceProcesses {
  constructor({ platform = process.platform, graceMs = 1000, forceWaitMs = 2500, runCommand = run } = {}) {
    this.platform = platform;
    this.graceMs = graceMs;
    this.forceWaitMs = forceWaitMs;
    this.runCommand = runCommand;
    this.children = new Map();
    this.stopping = null;
  }

  spawn(command, args, options) {
    if (this.stopping) throw new Error('后台服务正在停止，请稍后重试。');
    const child = spawn(command, args, { ...options, detached: this.platform !== 'win32', windowsHide: true });
    if (child.pid) {
      const entry = { pid: child.pid, child, groups: new Set([child.pid]) };
      this.children.set(child.pid, entry);
      child.once('exit', () => {
        // A POSIX service may exit before its descendants: retain the group.
        try {
          if (!this.alive(entry)) this.children.delete(entry.pid);
        } catch (error) {
          console.error('[youju] cannot inspect service process', error);
        }
      });
    }
    return child;
  }

  alive(entry) {
    const ids = this.platform === 'win32' ? [entry.pid] : [...entry.groups].map(pid => -pid);
    for (const pid of ids) {
      try {
        process.kill(pid, 0);
        return true;
      } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    }
    return false;
  }

  captureGroups(entry, output) {
    const rows = output.trim().split('\n').map(line => line.trim().split(/\s+/).map(Number))
      .filter(row => row.length === 3 && row.every(Number.isInteger));
    const descendants = new Set([entry.pid]);
    let added = true;
    while (added) {
      added = false;
      for (const [pid, parent] of rows) {
        if (!descendants.has(pid) && descendants.has(parent)) {
          descendants.add(pid);
          added = true;
        }
      }
    }
    for (const [pid, , group] of rows) {
      // A detached browser starts its own group. Never signal a shared group.
      if (pid > 1 && descendants.has(pid) && pid === group) entry.groups.add(group);
    }
  }

  signal(entry, signal) {
    for (const group of entry.groups) {
      try { process.kill(-group, signal); }
      catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
  }

  async stopEntry(entry) {
    if (this.platform === 'win32') {
      try {
        await this.runCommand('taskkill.exe', ['/PID', String(entry.pid), '/T', '/F'], { windowsHide: true, timeout: 5000 });
      } catch (error) {
        if (this.alive(entry)) throw error;
      }
    } else {
      const tree = await this.runCommand('ps', ['-A', '-o', 'pid=', '-o', 'ppid=', '-o', 'pgid='], { timeout: 3000 });
      this.captureGroups(entry, tree.stdout);
      this.signal(entry, 'SIGTERM');
      const until = Date.now() + this.graceMs;
      while (this.alive(entry) && Date.now() < until) await pause(40);
      // Still signal the group if its root has exited but descendants survived.
      if (this.alive(entry)) this.signal(entry, 'SIGKILL');
    }
    const deadline = Date.now() + this.forceWaitMs;
    while (this.alive(entry) && Date.now() < deadline) await pause(40);
    if (this.alive(entry)) throw new Error(`后台进程 ${entry.pid} 未能停止，已取消退出。`);
    this.children.delete(entry.pid);
  }

  stopAll() {
    if (this.stopping) return this.stopping;
    this.stopping = (async () => {
      const results = await Promise.allSettled([...this.children.values()].map(entry => this.stopEntry(entry)));
      const errors = results.filter(result => result.status === 'rejected').map(result => result.reason);
      if (errors.length) throw new AggregateError(errors, '部分后台服务未能停止。');
    })().finally(() => { this.stopping = null; });
    return this.stopping;
  }

  forceStopSync() {
    for (const entry of this.children.values()) {
      try {
        if (this.platform === 'win32') {
          spawnSync('taskkill.exe', ['/PID', String(entry.pid), '/T', '/F'], { windowsHide: true, timeout: 5000 });
        } else {
          const tree = spawnSync('ps', ['-A', '-o', 'pid=', '-o', 'ppid=', '-o', 'pgid='], { encoding: 'utf8', timeout: 3000 });
          if (tree.stdout) this.captureGroups(entry, tree.stdout);
          this.signal(entry, 'SIGKILL');
        }
      } catch (error) {
        console.error('[youju] forced service cleanup failed', error);
      }
    }
  }
}
