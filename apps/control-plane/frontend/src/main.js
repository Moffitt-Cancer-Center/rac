import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import React from 'react';
import ReactDOM from 'react-dom/client';
const App = () => {
    return (_jsxs("div", { children: [_jsx("h1", { children: "RAC Control Plane" }), _jsx("p", { children: "Welcome to the Research Application Commons submission portal." })] }));
};
const rootElement = document.getElementById('root');
if (rootElement) {
    ReactDOM.createRoot(rootElement).render(_jsx(React.StrictMode, { children: _jsx(App, {}) }));
}
