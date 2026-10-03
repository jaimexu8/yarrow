/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config) => {
    // pdf.js (the document viewer) references the optional Node "canvas"
    // package for server-side rendering, which the browser build never uses.
    config.resolve.alias.canvas = false;
    return config;
  },
};

export default nextConfig;
