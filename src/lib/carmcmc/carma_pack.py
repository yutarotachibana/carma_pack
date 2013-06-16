__author__ = 'Brandon C. Kelly'

import numpy as np
import matplotlib.pyplot as plt
from scipy.linalg import solve
from os import environ
import yamcmcpp.samplers as samplers


class CarSample(samplers.MCMCSample):
    """
    Class for storing and analyzing the MCMC samples of a CAR(p) model.
    """

    def __init__(self, time, y, ysig, filename=None, trace=None):
        """
        Constructor for the class. Right same as its superclass.

        :param filename: A string of the name of the file containing the MCMC samples generated by carpack.
        """
        self.time = time  # The time values of the time series
        self.y = y  # The measured values of the time series
        self.ysig = ysig  # The standard deviation of the measurement errors of the time series
        super(CarSample, self).__init__(filename=filename, trace=trace)

        # now calculate the CAR(p) characteristic polynomial roots, coefficients, and amplitude of driving noise and
        # add them to the MCMC samples
        print "Calculating roots of AR polynomial..."
        self._ar_roots()
        print "Calculating coefficients of AR polynomial..."
        self._ar_coefs()
        print "Calculating sigma..."
        self._sigma_noise()

        # make the parameter names (i.e., the keys) public so the use knows how to get them
        self.parameters = self._samples.keys()

    def generate_from_trace(self, trace):
        # Figure out how many AR terms we have
        self.p = trace.shape[1] - 2
        names = ['logpost', 'var', 'measerr_scale', 'log_centroid', 'log_width']
        if names != self._samples.keys():
            idx = 0
            # Parameters are not already in the dictionary, add them.
            # Not being returned at this point in time
            #self._samples['logpost']      = trace[:, idx]     # log-posterior of the CAR(p) model

            self._samples['var'] = trace[:, 0] ** 2  # Variance of the CAR(p) process
            self._samples['measerr_scale'] = trace[:, 1]       # Measurement errors are scaled by this much.
            ar_index = np.arange(0, self.p - 1, 2)
            # The centroids and widths of the quasi-periodic oscillations, i.e., of the Lorentzians characterizing
            # the power spectrum. Note that these are equal to -1 / 2 * pi times the imaginary and real parts of the
            # roots of the AR(p) characteristic polynomial, respectively.
            self._samples['log_centroid'] = trace[:, 2 + ar_index]
            if self.p % 2 == 1:
                # Odd number of roots, so add in low-frequency component
                ar_index = np.append(ar_index, ar_index.max() + 1)
            self._samples['log_width'] = trace[:, 3 + ar_index]

    def generate_from_file(self, filename):
        """
        Build the dictionary of parameter samples from an ascii file of MCMC samples from carpack.

        :param filename: The name of the file containing the MCMC samples generated by carpack.
        """
        # TODO: put in exceptions to make sure files are ready correctly
        # Grab the MCMC output
        trace = np.genfromtxt(filename[0], skip_header=1)
        self.generate_from_trace(trace)

    def _ar_roots(self):
        """
        Calculate the roots of the CAR(p) characteristic polynomial and add them to the MCMC samples.
        """
        var = self._samples['var']
        qpo_centroid = np.exp(self._samples['log_centroid'])
        qpo_width = np.exp(self._samples['log_width'])

        ar_roots = np.empty((var.size, self.p), dtype=complex)
        for i in xrange(self.p / 2):
            ar_roots[:, 2 * i] = qpo_width[:, i] + 1j * qpo_centroid[:, i]
            ar_roots[:, 2 * i + 1] = np.conjugate(ar_roots[:, 2 * i])
        if self.p % 2 == 1:
            # p is odd, so add in low-frequency component
            ar_roots[:, -1] = qpo_width[:, -1]

        # add it to the MCMC samples
        self._samples['ar_roots'] = -2.0 * np.pi * ar_roots

    def _ar_coefs(self):
        """
        Calculate the CAR(p) autoregressive coefficients and add them to the MCMC samples.
        """
        roots = self._samples['ar_roots']
        coefs = np.empty((roots.shape[0], self.p + 1), dtype=complex)
        for i in xrange(roots.shape[0]):
            coefs[i, :] = np.poly(roots[i, :])

        self._samples['ar_coefs'] = coefs.real

    def _sigma_noise(self):
        """
        Calculate the MCMC samples of the standard deviation of the white noise driving process and add them to the
        MCMC samples.
        """
        # get the CAR(p) model variance of the time series
        var = self._samples['var']

        # get the roots of the AR(p) characteristic polynomial
        ar_roots = self._samples['ar_roots']

        # calculate the variance of a CAR(p) process, assuming sigma = 1.0
        sigma1_variance = 0.0
        for k in xrange(self.p):
            denom_product = -2.0 * ar_roots[:, k].real + 0j
            for l in xrange(self.p):
                if l != k:
                    denom_product *= (ar_roots[:, l] - ar_roots[:, k]) * (np.conjugate(ar_roots[:, l]) + ar_roots[:, k])
            sigma1_variance += 1.0 / denom_product

        sigma = var / sigma1_variance.real

        # add the sigmas to the MCMC samples
        self._samples['sigma'] = np.sqrt(sigma)

    def plot_power_spectrum(self, percentile=68.0, nsamples=None, plot_log=True):
        """
        Plot the posterior median and the credibility interval corresponding to percentile of the CAR(p) PSD. This
        function returns a tuple containing the lower and upper PSD credibility intervals as a function of
        frequency, the median PSD as a function of frequency, and the frequencies.
        
        :rtype : A tuple of numpy arrays, (lower PSD, upper PSD, median PSD, frequencies).
        :param percentile: The percentile of the PSD credibility interval to plot.
        :param nsamples: The number of MCMC samples to use to estimate the credibility interval. The default is all
                         of them.
        :param plot_log: A boolean. If true, then a logarithmic plot is made.
        """
        sigmas = self._samples['sigma']
        ar_coefs = self._samples['ar_coefs']
        if nsamples is None:
            # Use all of the MCMC samples
            nsamples = sigmas.shape[0]
        else:
            try:
                nsamples <= sigmas.shape[0]
            except ValueError:
                "nsamples must be less than the total number of MCMC samples."

            nsamples0 = sigmas.shape[0]
            index = np.arange(nsamples) * (nsamples0 / nsamples)
            sigmas = sigmas[index]
            ar_coefs = ar_coefs[index]

        nfreq = 1000
        dt_min = self.time[1:] - self.time[0:self.time.size - 1]
        dt_min = dt_min.min()
        dt_max = self.time.max() - self.time.min()

        # Only plot frequencies corresponding to time scales a factor of 2 shorter and longer than the minimum and
        # maximum time scales probed by the time series.
        freq_max = 1.0 / (dt_min / 2.0)
        freq_min = (1.0 / (2.0 * dt_max))

        frequencies = np.linspace(np.log(freq_min), np.log(freq_max), num=nfreq)
        frequencies = np.exp(frequencies)
        psd_credint = np.empty((nfreq, 3))

        lower = (100.0 - percentile) / 2.0  # lower and upper intervals for credible region
        upper = 100.0 - lower

        # Compute the PSDs from the MCMC samples
        for i in xrange(nfreq):
            omega = 2.0 * np.pi * 1j * frequencies[i]
            ar_poly = np.zeros(nsamples, dtype=complex)
            for k in xrange(self.p - 1):
                # Here we compute:
                #   alpha(omega) = ar_coefs[0] * omega^p + ar_coefs[1] * omega^(p-1) + ... + ar_coefs[p]
                # Note that ar_coefs[0] = 1.0.
                ar_poly += ar_coefs[:, k] * omega ** (self.p - k)
            ar_poly += ar_coefs[:, self.p - 1] * omega + ar_coefs[:, self.p]
            psd_samples = sigmas ** 2 / np.abs(ar_poly) ** 2

            # Now compute credibility interval for power spectrum
            psd_credint[i, 0] = np.percentile(psd_samples, lower)
            psd_credint[i, 2] = np.percentile(psd_samples, upper)
            psd_credint[i, 1] = np.median(psd_samples)

        # Plot the power spectra
        plt.subplot(111)
        if plot_log:
            # plot the posterior median first
            plt.loglog(frequencies, psd_credint[:, 1], 'b')
        else:
            plt.plot(frequencies, psd_credint[:, 1], 'b')

        plt.fill_between(frequencies, psd_credint[:, 2], psd_credint[:, 0], facecolor='blue', alpha=0.5)
        plt.xlim(frequencies.min(), frequencies.max())
        plt.xlabel('Frequency')
        plt.ylabel('Power Spectrum')

        return (psd_credint[:, 0], psd_credint[:, 2], psd_credint[:, 1], frequencies)

    def assess_fit(self, bestfit="median"):
        """
        Display plots and provide useful information for assessing the quality of the CAR(p) model fit.

        :param bestfit: A string specifying how to define 'best-fit'. Can be the Maximum Posterior (MAP), the posterior
            mean ("mean") or the posterior median ("median").
        """
        bestfit = bestfit.lower()
        try:
            bestfit in ['map', 'median', 'mean']
        except ValueError:
            "bestfit must be one of 'map, 'median', or 'mean'"

        if bestfit == 'map':
            # use maximum a posteriori estimate
            max_index = self._samples['logpost'].argmax()
            sigsqr = self._samples['sigma'][max_index] ** 2
            ar_roots = self._samples['ar_roots'][max_index]
        elif bestfit == 'median':
            # use posterior median estimate
            sigsqr = np.median(self._samples['sigma']) ** 2
            ar_roots = np.median(self._samples['ar_roots'], axis=0)
        else:
            # use posterior mean as the best-fit
            sigsqr = np.mean(self._samples['sigma'] ** 2)
            ar_roots = np.mean(self._samples['ar_roots'], axis=0)

        # compute the kalman filter
        kalman_filter = KalmanFilter(self.time, self.y - self.y.mean(), self.ysig ** 2, sigsqr, ar_roots)
        kalman_mean, kalman_var = kalman_filter.filter()

        standardized_residuals = (self.y - self.y.mean() - kalman_mean) / np.sqrt(kalman_var)

        # plot the time series and kalman filter
        plt.subplot(221)
        plt.plot(self.time, self.y, 'k.', label='Data')
        plt.plot(self.time, kalman_mean + self.y.mean(), '-r', label='Kalman Filter')
        plt.xlabel('Time')
        plt.xlim(self.time.min(), self.time.max())
        #plt.legend()

        # plot the standardized residuals and compare with the standard normal
        plt.subplot(222)
        plt.plot(self.time, standardized_residuals, '.k')
        plt.xlabel('Time')
        plt.xlim(self.time.min(), self.time.max())

        # Now add the histogram of values to the standardized residuals plot
        pdf, bin_edges = np.histogram(standardized_residuals, bins=10)
        bin_edges = bin_edges[0:pdf.size]
        # Stretch the PDF so that it is readable on the residual plot when plotted horizontally
        pdf = pdf / float(pdf.max()) * 0.4 * self.time.max()
        # Add the histogram to the plot
        plt.barh(bin_edges, pdf, height=bin_edges[1] - bin_edges[0], alpha=0.75)
        # now overplot the expected standard normal distribution
        expected_pdf = np.exp(-0.5 * bin_edges ** 2)
        expected_pdf = expected_pdf / expected_pdf.max() * 0.4 * self.time.max()
        plt.plot(expected_pdf, bin_edges, '-r', lw=2)

        # plot the autocorrelation function of the residuals and compare with the 95% confidence intervals for white
        # noise
        plt.subplot(223)
        maxlag = 20
        lags, acf, not_needed1, not_needed2 = plt.acorr(standardized_residuals, maxlags=maxlag, lw=2)
        wnoise_upper = 1.96 / np.sqrt(self.time.size)
        wnoise_lower = -1.96 / np.sqrt(self.time.size)
        plt.fill_between([0, maxlag], wnoise_upper, wnoise_lower, alpha=0.5, facecolor='grey')
        plt.xlim(0, maxlag)
        plt.xlabel('Time Lag')
        plt.ylabel('ACF of Residuals')

        # plot the autocorrelation function of the squared residuals and compare with the 95% confidence intervals for
        # white noise
        plt.subplot(224)
        squared_residuals = standardized_residuals ** 2
        lags, acf, not_needed1, not_needed2 = plt.acorr(squared_residuals - squared_residuals.mean(), maxlags=maxlag,
                                                        lw=2)
        wnoise_upper = 1.96 / np.sqrt(self.time.size)
        wnoise_lower = -1.96 / np.sqrt(self.time.size)
        plt.fill_between([0, maxlag], wnoise_upper, wnoise_lower, alpha=0.5, facecolor='grey')
        plt.xlim(0, maxlag)
        plt.xlabel('Time Lag')
        plt.ylabel('ACF of Sqrd. Resid.')

    def predict_lightcurve(self, time, bestfit='median'):
        """
        Return the predicted value of the lightcurve and its standard deviation at the input time(s) given the best-fit
        value of the CARMA(p,q) model and the measured lightcurve.

        :param time: A scalar or numpy array containing the time values to predict the time series at.
        :param bestfit: A string specifying how to define 'best-fit'. Can be the Maximum Posterior (MAP), the posterior
            mean ("mean") or the posterior median ("median").
        """
        bestfit = bestfit.lower()
        try:
            bestfit in ['map', 'median', 'mean']
        except ValueError:
            "bestfit must be one of 'map, 'median', or 'mean'"

        if bestfit == 'map':
            # use maximum a posteriori estimate
            max_index = self._samples['logpost'].argmax()
            sigsqr = self._samples['sigma'][max_index] ** 2
            ar_roots = self._samples['ar_roots'][max_index]
        elif bestfit == 'median':
            # use posterior median estimate
            sigsqr = np.median(self._samples['sigma']) ** 2
            ar_roots = np.median(self._samples['ar_roots'], axis=0)
        else:
            # use posterior mean as the best-fit
            sigsqr = np.mean(self._samples['sigma'] ** 2)
            ar_roots = np.mean(self._samples['ar_roots'], axis=0)

        # note that KalmanFilter class assumes the time series has zero mean
        kalman_filter = KalmanFilter(self.time, self.y - self.y.mean(), self.ysig ** 2, sigsqr, ar_roots)

        if np.isscalar(time):
            yhat, yhat_var = kalman_filter.predict(time)
        else:
            yhat = np.empty(time.size)
            yhat_var = np.empty(time.size)
            for i in xrange(time.size):
                yhati, yhat_vari = kalman_filter.predict(time[i])
                yhat[i] = yhati
                yhat_var[i] = yhat_var

        yhat += self.y.mean()  # add mean back into time series

        return yhat, yhat_var

    def simulate_lightcurve(self, time, bestfit='median'):
        """
        Simulate a lightcurve at the input time(s) given the best-fit value of the CARMA(p,q) model and the measured
        lightcurve.

        :param time: A scalar or numpy array containing the time values to simulate the time series at.
        :param bestfit: A string specifying how to define 'best-fit'. Can be the Maximum Posterior (MAP), the posterior
            mean ("mean") or the posterior median ("median").
        """
        bestfit = bestfit.lower()
        try:
            bestfit in ['map', 'median', 'mean']
        except ValueError:
            "bestfit must be one of 'map, 'median', or 'mean'"

        if bestfit == 'map':
            # use maximum a posteriori estimate
            max_index = self._samples['logpost'].argmax()
            sigsqr = self._samples['sigma'][max_index] ** 2
            ar_roots = self._samples['ar_roots'][max_index]
        elif bestfit == 'median':
            # use posterior median estimate
            sigsqr = np.median(self._samples['sigma']) ** 2
            ar_roots = np.median(self._samples['ar_roots'], axis=0)
        else:
            # use posterior mean as the best-fit
            sigsqr = np.mean(self._samples['sigma'] ** 2)
            ar_roots = np.mean(self._samples['ar_roots'], axis=0)

        # note that KalmanFilter class assumes the time series has zero mean
        kalman_filter = KalmanFilter(self.time, self.y - self.y.mean(), self.ysig ** 2, sigsqr, ar_roots)

        ysim = kalman_filter.simulate(time)
        ysim += self.y.mean()  # add mean back into time series

        return ysim


class KalmanFilter(object):
    def __init__(self, time, y, yvar, sigsqr, ar_roots):
        """
        Constructor for Kalman Filter class.

        :param time: The time values of the time series.
        :param y: The mean-subtracted time series.
        :param yvar: The variance in the measurement errors on the time series.
        :param sigsqr: The variance of the driving white noise term in the CAR(p) process.
        :param ar_roots: The roots of the autoregressive characteristic polynomial.
        """
        self.time = time
        self.y = y
        self.yvar = yvar
        self.sigsqr = sigsqr
        self.ar_roots = ar_roots
        self.p = ar_roots.size  # order of the CAR(p) process

    def reset(self):
        """
        Reset the Kalman Filter to its initial state.
        """
        # Setup the matrix of Eigenvectors for the Kalman Filter transition matrix. This allows us to transform
        # quantities into the rotated state basis, which makes the computations for the Kalman filter easier and faster.
        EigenMat = np.ones((self.p, self.p), dtype=complex)
        EigenMat[1, :] = self.ar_roots
        for k in xrange(2, self.p):
            EigenMat[k, :] = self.ar_roots ** k

        # Input vector under the original state space representation
        Rvector = np.zeros(self.p, dtype=complex)
        Rvector[-1] = 1.0

        # Input vector under rotated state space representation
        Jvector = solve(EigenMat, Rvector)  # J = inv(E) * R

        # Compute the vector of moving average coefficients in the rotated state.
        rotated_MA_coefs = np.ones(self.p, dtype=complex)  # just ones for a CAR(p) model

        # Calculate the stationary covariance matrix of the state vector
        StateVar = np.empty((self.p, self.p), dtype=complex)
        for j in xrange(self.p):
            StateVar[:, j] = -self.sigsqr * Jvector * np.conjugate(Jvector[j]) / \
                             (self.ar_roots + np.conjugate(self.ar_roots[j]))

        # Initialize variance in one-step prediction error and the state vector
        PredictionVar = StateVar.copy()
        StateVector = np.zeros(self.p, dtype=complex)

        # Initialize the Kalman mean and variance. These are the forecasted values and their variances.
        self.kalman_mean = np.empty_like(self.time)
        self.kalman_var = np.empty_like(self.time)
        self.kalman_mean[0] = 0.0
        self.kalman_var[0] = PredictionVar.sum().real + self.yvar[0]  # Kalman variance must be a real number

        # Initialize the innovations, i.e., the KF residuals
        self._innovation = self.y[0]

        # Convert the current state to matrices for convenience, since we'll be doing some Linear algebra.
        self._StateVector = np.matrix(StateVector).T
        self._StateVar = np.matrix(StateVar)
        self._PredictionVar = np.matrix(PredictionVar)
        self._rotated_MA_coefs = np.matrix(rotated_MA_coefs)  # this is a row vector, so no transpose
        self._StateTransition = np.zeros_like(self._StateVector)
        self._KalmanGain = np.zeros_like(self._StateVector)
        self._current_index = 0

    def update(self):
        """
        Perform one iteration (update) of the Kalman Filter.
        """
        # First compute the Kalman gain
        self._KalmanGain = self._PredictionVar.sum(axis=1) / self.kalman_var[self._current_index - 1]
        # update the state vector
        self._StateVector += self._innovation * self._KalmanGain
        # update the state one-step prediction error variance
        self._PredictionVar -= self.kalman_var[self._current_index - 1] * (self._KalmanGain * self._KalmanGain.H)
        # predict the next state, do element-wise multiplication
        dt = self.time[self._current_index] - self.time[self._current_index - 1]
        self._StateTransition = np.matrix(np.exp(self.ar_roots * dt)).T
        self._StateVector = np.multiply(self._StateVector, self._StateTransition)
        # update the predicted state covariance matrix
        self._PredictionVar = np.multiply(self._StateTransition * self._StateTransition.H,
                                          self._PredictionVar - self._StateVar) + self._StateVar
        # now predict the observation and its variance
        self.kalman_mean[self._current_index] = self._StateVector.sum().real
        # for a CARMA(p,q) model we need to add the rotated MA terms
        self.kalman_var[self._current_index] = self._PredictionVar.sum().real + self.yvar[self._current_index]
        # finally, update the innovation
        self._innovation = self.y[self._current_index] - self.kalman_mean[self._current_index]
        self._current_index += 1

    def filter(self):
        """
        Perform the Kalman Filter on all points of the time series. The kalman mean and variance are returned upon
        completion, and are stored in the instantiated KalmanFilter object.
        """
        self.reset()
        for i in xrange(self.time.size):
            self.update()

        return self.kalman_mean, self.kalman_var

    def predict(self, time_predict):
        """
        Return the predicted value of a lightcurve and its standard deviation at the input time given the input
        values of the CARMA(p,q) model parameters and a measured lightcurve.

        :rtype : A tuple containing the predicted value and its variance.
        :param time_predict: The time at which to predict the lightcurve.
        """
        try:
            self.time.min() < time_predict
        except ValueError:
            "backcasting currently not supported: time_predict must be greater than self.time.min()"

        self.reset()
        # find the index where time[ipredict-1] < time_predict < time[ipredict]
        ipredict = np.max(np.where(self.time < time_predict)) + 1
        for i in xrange(ipredict):
            # run the kalman filter for time < time_predict
            self.update()

        # predict the value of y[time_predict]
        self._KalmanGain = self._PredictionVar * self._rotated_MA_coefs.H / self.kalman_var[ipredict-1]
        self._StateVector += self._innovation * self._KalmanGain
        self._PredictionVar -= self.kalman_var[ipredict-1] * (self._KalmanGain * self._KalmanGain.H)
        dt = time_predict - self.time[ipredict-1]
        self._StateTransition = np.matrix(np.exp(self.ar_roots * dt)).T
        self._StateVector = np.multiply(self._StateVector, self._StateTransition)
        self._PredictionVar = np.multiply(self._StateTransition * self._StateTransition.H,
                                          self._PredictionVar - self._StateVar) + self._StateVar

        ypredict_mean = np.real(self._rotated_MA_coefs * self._StateVector)
        ypredict_var = np.real(self._rotated_MA_coefs * self._PredictionVar * self._rotated_MA_coefs.H)

        # start the running statistics for the conditional mean and precision of the predicted time series value, given
        # the measured time series
        cprecision = 1.0 / ypredict_var
        cmean = cprecision * ypredict_mean

        if ipredict >= self.time.size:
            # we are forecasting (extrapolating) the value, so no need to run interpolation steps below
            return ypredict_mean, ypredict_var

        # for time > time_predict we need to compute the coefficients for the linear filter, i.e., at time[j]:
        # E(y[j]|{y[i]; j<i}) = alpha[j] + beta[j] * ypredict. we do this using recursions similar to the kalman
        # filter.

        # first set the initial values.
        self._KalmanGain = self._PredictionVar * self._rotated_MA_coefs.H / ypredict_var
        # initialize the coefficients for predicting the state vector at coefs(time_predict|time_predict)
        const_state = self._StateVector - self._KalmanGain * ypredict_mean
        slope_state = self._KalmanGain
        # update the state one-step prediction error variance
        self._PredictionVar -= ypredict_var * (self._KalmanGain * self._KalmanGain.H)
        # do coefs(time_predict|time_predict) --> coefs(time[i+1]|time_predict)
        dt = self.time[ipredict] - time_predict
        self._StateTransition = np.matrix(np.exp(self.ar_roots * dt)).T
        const_state = np.multiply(const_state, self._StateTransition)
        slope_state = np.multiply(slope_state, self._StateTransition)
        # update the predicted state covariance matrix
        self._PredictionVar = np.multiply(self._StateTransition * self._StateTransition.H,
                                          self._PredictionVar - self._StateVar) + self._StateVar
        # compute the coefficients for the linear filter at time[ipredict], and compute the variance in the predicted
        # y[ipredict]
        const = np.real(self._rotated_MA_coefs * const_state)
        slope = np.real(self._rotated_MA_coefs * slope_state)
        self.kalman_var[ipredict] = \
            np.real(self._rotated_MA_coefs * self._PredictionVar * self._rotated_MA_coefs.H) + \
            self.yvar[ipredict]

        # update the running conditional mean and variance of the predicted time series value
        cprecision += slope ** 2 / self.kalman_var[ipredict]
        cmean += slope * (self.y[ipredict] - const) / self.kalman_var[ipredict]

        # now repeat for time > time_predict
        for i in xrange(ipredict+1, self.time.size):
            self._KalmanGain = self._PredictionVar * self._rotated_MA_coefs.H / self.kalman_var[i-1]
            # update the state prediction coefficients: coefs(i|i-1) --> coefs(i|i)
            const_state += self._KalmanGain * (self.y[i] - const)
            slope_state -= self._KalmanGain * slope
            # update the state one-step prediction error variance
            self._PredictionVar -= self.kalman_var[i-1] * (self._KalmanGain * self._KalmanGain.H)
            # compute the one-step state prediction coefficients: coefs(i|i) --> coefs(i+1|i)
            dt = time_predict - self.time[i-1]
            self._StateTransition = np.matrix(np.exp(self.ar_roots * dt)).T
            const_state = np.multiply(const_state, self._StateTransition)
            slope_state = np.multiply(slope_state, self._StateTransition)
            # compute the state one-step prediction error variance
            self._PredictionVar = np.multiply(self._StateTransition * self._StateTransition.H,
                                              self._PredictionVar - self._StateVar) + self._StateVar
            # compute the coefficients for predicting y[i]|y[j],j<i as a function of ypredict
            const = np.real(self._rotated_MA_coefs * const_state)
            slope = np.real(self._rotated_MA_coefs * slope_state)
            # compute the variance in predicting y[i]|y[j],j<i
            self.kalman_var[i] = \
                np.real(self._rotated_MA_coefs * self._PredictionVar * self._rotated_MA_coefs.H) + \
                self.yvar[i]
            # finally, update the running conditional mean and variance of the predicted time series value
            cprecision += slope ** 2 / self.kalman_var[i]
            cmean += slope * (self.y[i] - const) / self.kalman_var[i]

        cvar = 1.0 / cprecision
        cmean *= cvar

        return cmean, cvar

    def simulate(self, time_simulate):
        """
        Simulate a lightcurve at the input time values of time_simulate, given the measured lightcurve and input
        CARMA(p,q) parameters.

        :rtype : A scalar or numpy array, depending on type of time_simulate.
        :param time_simulate: The time(s) at which to simulate a random draw of the lightcurve conditional on the
            measured time series and the input parameters.
        """

        if np.isscalar(time_simulate):
            cmean, cvar = self.predict(time_simulate)
            ysimulated = np.random.normal(cmean, np.sqrt(cvar))
            return ysimulated
        else:
            # input is array-like, need to simulate values sequentially, adding each value to the measured time series
            # as they are simulated
            time0 = self.time  # save original values
            y0 = self.y
            yvar0 = self.yvar
            ysimulated = np.empty(self.time.size)
            time_simulate.sort()
            for i in xrange(time_simulate.size):
                cmean, cvar = self.predict(time_simulate[i])
                ysimulated[i] = np.random.normal(cmean, np.sqrt(cvar))  # simulate the time series value
                # find the index where time[isimulate-1] < time_simulate < time[isimulate]
                isimulate = np.max(np.where(self.time < time_simulate[i])) + 1
                # insert the simulated value into the time series array
                self.time = np.insert(self.time, isimulate, time_simulate[i])
                self.y = np.insert(self.y, isimulate, ysimulated[i])
                self.yvar = np.insert(self.yvar, isimulate, 0.0)

            # reset measured time series to original values
            self.y = y0
            self.time = time0
            self.yvar = yvar0

        return ysimulated



def get_ar_roots(qpo_width, qpo_centroid):
    """
Return the roots of the characteristic polynomial of the CAR(p) process, given the lorentzian widths and centroids.

:rtype : a numpy array
:param qpo_width: The widths of the lorentzian functions defining the PSD.
:param qpo_centroid: The centroids of the lorentzian functions defining the PSD.
"""
    p = qpo_centroid.size + qpo_width.size
    ar_roots = np.empty(p, dtype=complex)
    for i in xrange(p / 2):
        ar_roots[2 * i] = qpo_width[i] + 1j * qpo_centroid[i]
        ar_roots[2 * i + 1] = np.conjugate(ar_roots[2 * i])
    if p % 2 == 1:
        # p is odd, so add in low-frequency component
        ar_roots[-1] = qpo_width[-1]

    return -2.0 * np.pi * ar_roots


def power_spectrum(freq, sigma, ar_coef):
    """
Return the power spectrum for a CAR(p) process calculated at the input frequencies.

:param freq: The frequencies at which to calculate the PSD.
:param sigma: The standard deviation driving white noise.
:param ar_coef: The CAR(p) model autoregressive coefficients.

:rtype : A numpy array.
"""
    ar_poly = np.polyval(ar_coef, 2.0 * np.pi * 1j * freq)  # Evaluate the polynomial in the PSD denominator
    pspec = sigma ** 2 / np.abs(ar_poly) ** 2
    return pspec


def carp_variance(sigsqr, ar_roots):
    """
Return the variance of a CAR(p) process.

:param sigsqr: The variance in the driving white noise.
:param ar_roots: The roots of the CAR(p) characteristic polynomial.
"""
    sigma1_variance = 0.0
    p = ar_roots.size
    for k in xrange(p):
        denom_product = -2.0 * ar_roots[k].real + 0j
        for l in xrange(p):
            if l != k:
                denom_product *= (ar_roots[l] - ar_roots[k]) * (np.conjugate(ar_roots[l]) + ar_roots[k])
        sigma1_variance += 1.0 / denom_product

    return sigsqr * sigma1_variance.real


def carp_process(time, sigsqr, ar_roots):
    """
Generate a CAR(p) process.

:param time: The time values to generate the CAR(p) process at.
:param sigsqr: The variance in the driving white noise term.
:param ar_roots: The roots of the CAR(p) characteristic polynomial.
:rtype : A numpy array containing the simulated CAR(p) process values at time.
"""
    p = ar_roots.size
    time.sort()
    # make sure process is stationary
    try:
        np.any(ar_roots.real < 0)
    except ValueError:
        "Process is not stationary, real part of roots must be negative."

    # make sure the roots are unique
    tol = 1e-8
    roots_grid = np.meshgrid(ar_roots, ar_roots)
    roots_grid1 = roots_grid[0].ravel()
    roots_grid2 = roots_grid[1].ravel()
    diff_roots = np.abs(roots_grid1 - roots_grid2) / np.abs(roots_grid1 + roots_grid2)
    try:
        np.any(diff_roots > tol)
    except ValueError:
        "Roots are not unique."

    # Setup the matrix of Eigenvectors for the state space transition matrix. This allows us to transform quantities
    # into the rotated state basis. We then proceed by simulating the rotated state vectors, which are Markovian, and
    # then constructing the CAR(p) process from a linear combination of the rotated state vectors.
    EigenMat = np.ones((p, p), dtype=complex)
    EigenMat[1, :] = ar_roots
    for k in xrange(2, p):
        EigenMat[k, :] = ar_roots ** k

    # Input vector under the original state space representation
    Rvector = np.zeros(p, dtype=complex)
    Rvector[-1] = 1.0

    # Input vector under rotated state space representation
    Jvector = solve(EigenMat, Rvector)  # J = inv(E) * R

    # Compute the vector of moving average coefficients in the rotated state.
    rotated_MA_coefs = np.ones(p, dtype=complex)  # just ones for a CAR(p) model

    # Calculate the stationary covariance matrix of the state vector
    StateVar = np.empty((p, p), dtype=complex)
    for j in xrange(p):
        StateVar[:, j] = -sigsqr * Jvector * np.conjugate(Jvector[j]) / (ar_roots + np.conjugate(ar_roots[j]))

    # Covariance matrix of real and imaginary components of the rotated state vector. The rotated state vector
    # follows a complex multivariate normal distribution
    ComplexCovar_top = np.hstack((StateVar.real, -StateVar.imag))
    ComplexCovar_bottom = np.hstack((StateVar.imag, StateVar.real))
    ComplexCovar = np.vstack((ComplexCovar_top, ComplexCovar_bottom))

    # generate the state vector at time[0] by drawing from its stationary distribution
    state_components = np.random.multivariate_normal(np.zeros(2 * p), ComplexCovar)
    state_vector = state_components[0:p] + 1j * state_components[p:]

    car_process = np.empty(time.size)
    # calculate first value of the CAR(p) process
    car_process[0] = np.real(np.sum(rotated_MA_coefs * state_vector))

    StateCvar = np.empty_like(
        StateVar)  # the state vector covariance matrix, conditional on the previous state vector

    # now generate remaining CAR(p) values
    for i in xrange(1, time.size):
        # update the state vector mean, conditional on the earlier value
        state_cmean = state_vector * np.exp(ar_roots * (time[i] - time[i - 1]))  # the state vector conditional mean

        # compute the state vector conditional covariance matrix
        for j in xrange(p):
            StateCvar[:, j] = StateVar[:, j] * (1.0 - np.exp((ar_roots + np.conjugate(ar_roots[j])) *
                                                             (time[i] - time[i - 1])))

        # update the covariance matrix of the state vector components
        ComplexCovar_top = np.hstack((StateCvar.real, -StateCvar.imag))
        ComplexCovar_bottom = np.hstack((StateCvar.imag, StateCvar.real))
        ComplexCovar = np.vstack((ComplexCovar_top, ComplexCovar_bottom))

        # now randomly generate a new value of the rotated state vector
        state_components = np.random.multivariate_normal(np.zeros(2 * p), ComplexCovar)
        state_vector = state_cmean + state_components[0:p] + 1j * state_components[p:]

        # next value of the CAR(p) process
        car_process[i] = np.real(np.sum(rotated_MA_coefs * state_vector))

    return car_process


    # dir = environ['HOME'] + '/Projects/carma_pack/test_data/'
    # data = np.genfromtxt(dir + 'car4_test.dat')
    # car = CarSample(data[:, 0], data[:, 1], data[:, 2], filename=dir + 'car4_test.out')
    # psdlo, psdhi, psdhat, freq = car.plot_power_spectrum(percentile=95.0)
    #
    # sigma0 = np.sqrt(0.25)
    # qpo_width0 = np.array([1.0/100.0, 1.0/100.0])
    # qpo_cent0 = np.array([1.0, 1.0/20.0])
    # ar_roots0 = get_ar_roots(qpo_width0, qpo_cent0)
    # ar_coef0 = np.poly(ar_roots0)
    #psd0 = power_spectrum(freq, sigma0, ar_coef0.real)

    #plt.plot(freq, psd0, 'r', lw=2)

    # print ar_roots0
    # dt = np.random.uniform(0.1, 1.0, 10000)
    # time = np.cumsum(dt)
    # time = time - np.min(time)
    # np.savetxt('time.txt', time, fmt='%10.5f')
    # carp = carp_process(time, sigma0 ** 2, ar_roots0)
    # kmean, kvar = kalman_filter(time, carp, np.zeros(10000), sigma0 ** 2, ar_roots0)
    # sresid = (carp - kmean) / np.sqrt(kvar)
    # print "Sigma in lightcurve:", np.std(carp), np.sqrt(carp_variance(sigma0 ** 2, ar_roots0))
    # plt.hist(sresid, bins=50, normed=True)
    # xgrid = np.linspace(-3.0, 3.0)
    # plt.plot(xgrid, 1.0 / np.sqrt(2.0 * 3.14) * np.exp(-0.5 * xgrid ** 2))
    # plt.show()
    #
    # lags, autocorr, line, other = plt.acorr(sresid, maxlags=2500)
    # wnoise_upper = 1.96 / np.sqrt(time.size)
    # wnoise_lower = -1.96 / np.sqrt(time.size)
    # plt.fill_between([0, 100], wnoise_upper, wnoise_lower, alpha=0.5, facecolor='grey')
    # plt.xlim([0, 100])
    # plt.show()
    # out_of_bounds = (autocorr < wnoise_lower) | (autocorr > wnoise_upper)
    # nacorr = autocorr.size / 2 - 1
    # out_of_bounds = np.sum(out_of_bounds) / 2 - 1
    # print "Total out of " + str(nacorr) + " of acorr value outside of 95% confidence region: ", out_of_bounds