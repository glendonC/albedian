// A file Vite serves and hands its address for (`import url from './file?url'`); the app's tsconfig carries no
// vite/client types.
declare module '*?url' {
  const url: string;
  export default url;
}
