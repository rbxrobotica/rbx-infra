"use strict";

// Local-only RBXNet client. Credentials and per-user network state live outside
// Git under THELOUNGE_HOME. Keep the HTTP listener on loopback: the container
// intentionally uses the host network so it can reach the SSH IRC tunnel.
module.exports = {
  public: false,
  host: "127.0.0.1",
  port: 9000,
  reverseProxy: false,
  maxHistory: 1000,
  prefetch: false,
  disableMediaPreview: true,
  prefetchStorage: false,
  fileUpload: {
    enable: false,
    maxFileSize: 10240,
    baseUrl: null,
  },
  defaults: {
    name: "RBXNet",
    host: "127.0.0.1",
    port: 6667,
    password: "",
    tls: false,
    rejectUnauthorized: true,
    nick: "leandro",
    username: "leandro",
    realname: "RBX operator",
    join: "#rbx-founder,#strategos,#missions,#robson",
    leaveMessage: "",
  },
  lockNetwork: true,
  // Ergo owns the authorized room history. Avoid a second content archive on
  // the desktop; the private Lounge session still keeps the IRC connection.
  messageStorage: [],
};
