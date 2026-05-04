const { AndroidConfig, withAndroidManifest, withDangerousMod } = require("@expo/config-plugins");
const fs = require("fs");
const path = require("path");

module.exports = function withCleartextTraffic(config) {
  config = withAndroidManifest(config, (config) => {
    const application = config.modResults.manifest.application?.[0];

    if (application) {
      application.$["android:usesCleartextTraffic"] = "true";
      application.$["android:networkSecurityConfig"] = "@xml/network_security_config";
    }

    return config;
  });

  return withDangerousMod(config, [
    "android",
    async (config) => {
      const resPath = await AndroidConfig.Paths.getResourceFolderAsync(config.modRequest.projectRoot);
      const xmlPath = path.join(resPath, "xml");
      fs.mkdirSync(xmlPath, { recursive: true });
      fs.writeFileSync(
        path.join(xmlPath, "network_security_config.xml"),
        `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="true" />
  <domain-config cleartextTrafficPermitted="true">
    <domain includeSubdomains="true">192.168.31.87</domain>
    <domain includeSubdomains="true">127.0.0.1</domain>
    <domain includeSubdomains="true">localhost</domain>
  </domain-config>
</network-security-config>
`
      );
      return config;
    }
  ]);
};
