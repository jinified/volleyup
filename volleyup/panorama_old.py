#!/usr/bin/env python
""" Performs image stitching on images provided, to create a panorama view """
import cv2
import numpy as np
import math
import config
import os
from utils import get_channel, get_jpgs, get_netmask, write_jpgs
from feature import FeatureDescriptor


def copyOver(source, destination):
    result_grey = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    ret, mask = cv2.threshold(result_grey, 10, 255, cv2.THRESH_BINARY)
    mask_inv = cv2.bitwise_not(mask)
    roi = cv2.bitwise_and(source, source, mask=mask)
    im2 = cv2.bitwise_and(destination, destination, mask=mask_inv)
    result = cv2.add(im2, roi)
    return result

class TranslationStitcher():
    """ Assumes only translation offset between images and credited to
        https://github.com/marcpare/stitch/blob/master/crichardt/stitch.py
        """
    def __init__(self, imgs):
        self.ft = FeatureDescriptor()
        self.imgs = imgs
    
    def calc_matches(self, desc1, desc2, method='flann'):
        """ Calculate matches between descriptors specified by given method
            Parameters
            ----------
            method : bf    (brute force matching)
            """
        """ Alternatively?
            matcher = cv2.DescriptorMatcher_create("BruteForce")
            rawMatches = matcher.knnMatch(desc1, desc2, 2)
            return rawMatches
            """
        bf = cv2.BFMatcher()
        return bf.knnMatch(desc1, desc2, 2)
    
    def match_features(self, desc1, desc2, ratio=0.3):
        """ Matches features and filter only good matches using Lowe's ratio """
        matches = self.calc_matches(desc1, desc2)
        good_matches = [m for m, n in matches if m.distance < ratio * n.distance]
        return good_matches
    
    def calc_translation(self, src_pts, dst_pts):
        m = cv2.estimateRigidTransform(src_pts, dst_pts, True)
        return np.float32([[1, 0, m[0, 2]],
                           [0, 1, 0]])
    
    def calc_homography(self, imgA, imgB, kp1, kp2, good_matches, min_good_match=4, reproj_thresh=3.0):
        """
            Calculates homography when there are at least 8 matched feature points (4 in each image)
            Parameters
            ----------
            min_good_match : minimum number of good matches before calculating homography
            reproj_thresh  : maximum allowed reprojection error for RANSAC to be treated as inlier
            """
        if len(good_matches) >= min_good_match:
            src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
            
            H, status = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, reproj_thresh)
            return (H, status)
        
        print "Not enough matches are found - %d/%d" % (len(good_matches), min_good_match)
        return None
    
    def calc_affine(self, src_pts, dst_pts):
        """
            Calculates affine transformation required for destination points of the destination image to match the source points
            """
        affine = self.calc_translation(dst_pts, src_pts)
        return affine
    
    def overlay_image(self, mainImage, overlayImage):
        """
            Overlays overlayImage onto mainImage, assuming that important parts of overlayImage are not totally black i.e. (0,0,0)
            """
        return np.where(overlayImage < [30,30,30], mainImage, overlayImage)
    
    def generate_panorama(self, mask_func, channel='hsv_s', feature='akaze'):
        panorama_img_list = []
        homographyStack = []
        """ Generates image panorama
            Parameters
            ----------
            mask_func : mask function to produce mask used to reduce search area of feature detection (currently, this is not used)
            channel   : channel used for processing
            feature   : feature detector for interest point detection
            """
        sift = cv2.xfeatures2d.SIFT_create()# testing with sift instead of akaze
        panorama_img = self.imgs[0]
        resultingHomography = None
        # Add in margins in all directions
        #cv2.imshow('Masked image', mask_func(panorama_img))
        #cv2.waitKey(10)
        imgA = panorama_img.copy()
        panorama_img = np.pad(panorama_img, ((0,100),(500,0),(0,0)), mode='constant')
        panorama_img_list.append(panorama_img.copy())
        for index, imgB in enumerate(self.imgs[1:]):
            #imgB = np.pad(imgB, ((300,300),(300,300),(0,0)), mode='constant')
            print "Processing image", index
            
            key_points_A, desc1 = self.ft.compute(imgA, feature, imgA)
            key_points_B, desc2 = self.ft.compute(imgB, feature, imgB)
            #key_points_A, desc1 = sift.detectAndCompute(mask_func(imgA), None)
            #key_points_B, desc2 = sift.detectAndCompute(mask_func(imgB), None)
            # Match feature descriptors and filter which keeps the good ones
            matching_features = self.match_features(desc2, desc1)
            
            matched = cv2.drawMatches(imgB, key_points_B, imgA, key_points_A, matching_features, None, flags=2)
            cv2.imshow('Matched', matched)
            cv2.waitKey(10)
            
            # Calculate the homography matrix and affine required to transform imgB to imgA (so that the matching points overlap)
            if len(matching_features) >= 4:
                (H, status) = self.calc_homography(imgB, imgA, key_points_B, key_points_A, matching_features)
            else:
                print "Not enough matching features"
            if H is not None:
                homographyStack.append(H)
                warpedB = imgB.copy()
                warpedB = np.pad(warpedB, ((0,100),(500,0),(0,0)), mode='constant')
                for ho in reversed(homographyStack[:-2]):
                    warpedB = cv2.warpPerspective(warpedB, ho, (warpedB.shape[1], warpedB.shape[0]))
                warpedB = cv2.warpPerspective(warpedB, homographyStack[0], (panorama_img.shape[1], panorama_img.shape[0]))
                #panorama_img = np.pad(panorama_img, ((100,500),(100,100),(0,0)), mode='constant')
                #panorama_img = self.overlay_image(panorama_img, warpedB)
                panorama_img = copyOver(warpedB, panorama_img)
                panorama_img_list.append(panorama_img.copy())
            imgA = imgB.copy()
        
        return panorama_img_list

def convertToVideo(dirpath):
    imgs = get_jpgs(dirpath)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    vw = cv2.VideoWriter("output_old.mov", fourcc, 30, (imgs[0].shape[1], imgs[0].shape[0]))#(imgs[0].shape[1]+400, imgs[0].shape[0]+400))
    print "VideoWriter is opened:", vw.isOpened()
    print("Writing video ...")
    i = 1
    for img in imgs:
        print "Writing image", i
        i+=1
        vw.write(img)
    
    vw.release()


if __name__ == '__main__':
    number = 1 # Change this number to perform the stitch on different segments
    ## Put extracted images into DATA_DIR/<folder> before running this
    imgs = get_jpgs(config.DATA_DIR + "beachVolleyball" + str(number) + "/")
    cv2.ocl.setUseOpenCL(False) # A workaround for ORB feature detector error
    stitcher = TranslationStitcher(imgs[::5])
    panorama_list = stitcher.generate_panorama(get_netmask)
    
    # Create the folder
    d = os.path.dirname(config.DATA_DIR + "processedImages" + str(number) + "/")
    if not os.path.exists(d):
        os.makedirs(d)
    else:
        filelist = os.listdir(d)
        for file in filelist:
            os.remove(d + "/" + file)
    
    write_jpgs(config.DATA_DIR + "processedImages" + str(number) + "/", jpgs=panorama_list)
    convertToVideo(config.DATA_DIR + "processedImages" + str(number) + "/")
    cv2.destroyAllWindows()