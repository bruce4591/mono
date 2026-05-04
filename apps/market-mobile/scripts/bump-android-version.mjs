import fs from "node:fs";
import path from "node:path";

const appRoot = path.resolve(import.meta.dirname, "..");
const packageJsonPath = path.join(appRoot, "package.json");
const packageLockPath = path.join(appRoot, "package-lock.json");
const appJsonPath = path.join(appRoot, "app.json");
const androidBuildGradlePath = path.join(appRoot, "android", "app", "build.gradle");

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function bumpPatch(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version);
  if (!match) {
    throw new Error(`Expected semantic version like 0.1.0, got ${version}`);
  }

  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}`;
}

function readNativeVersionCode() {
  if (!fs.existsSync(androidBuildGradlePath)) {
    return null;
  }

  const gradle = fs.readFileSync(androidBuildGradlePath, "utf8");
  const match = /versionCode\s+(\d+)/.exec(gradle);
  return match ? Number(match[1]) : null;
}

const packageJson = readJson(packageJsonPath);
const appJson = readJson(appJsonPath);
const currentVersion = appJson.expo?.version ?? packageJson.version;
const nextVersion = process.env.MARKET_MOBILE_VERSION || bumpPatch(currentVersion);
const currentVersionCode =
  appJson.expo?.android?.versionCode ?? readNativeVersionCode() ?? 1;
const nextVersionCode = Number(process.env.MARKET_MOBILE_VERSION_CODE || currentVersionCode + 1);

packageJson.version = nextVersion;
writeJson(packageJsonPath, packageJson);

if (fs.existsSync(packageLockPath)) {
  const packageLock = readJson(packageLockPath);
  packageLock.version = nextVersion;
  if (packageLock.packages?.[""]) {
    packageLock.packages[""].version = nextVersion;
  }
  writeJson(packageLockPath, packageLock);
}

appJson.expo.version = nextVersion;
appJson.expo.android = appJson.expo.android ?? {};
appJson.expo.android.versionCode = nextVersionCode;
writeJson(appJsonPath, appJson);

if (fs.existsSync(androidBuildGradlePath)) {
  const gradle = fs
    .readFileSync(androidBuildGradlePath, "utf8")
    .replace(/versionCode\s+\d+/, `versionCode ${nextVersionCode}`)
    .replace(/versionName\s+"[^"]+"/, `versionName "${nextVersion}"`);
  fs.writeFileSync(androidBuildGradlePath, gradle);
}

console.log(`market-mobile version ${currentVersion} -> ${nextVersion}`);
console.log(`Android versionCode ${currentVersionCode} -> ${nextVersionCode}`);
